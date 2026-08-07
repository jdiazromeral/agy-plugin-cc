"""Tests for companion.cancel — the `cancel` subcommand that terminates a
running background **job** by its recorded PID/process group and
transitions its job record to a terminal `cancelled` status.

Driven directly over `companion.cancel`'s public functions with a stub
`terminate` seam and job records constructed on disk — no real process is
ever spawned or killed. `_terminate`'s own stale-PID (ESRCH) handling is
exercised by monkeypatching `os.killpg`/`os.kill`, per the Method: the
stale-PID and already-terminal edge cases come first, before the general
happy path.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import cancel, state  # noqa: E402
from companion.status import STATUS_CANCELLED, STATUS_RUNNING  # noqa: E402

_BOUND_UUID = "22222222-2222-2222-2222-222222222222"
# Pinned wherever a test reaches the kill path. The real _await_exit probes
# the OS for the PID it is given, and these tests use invented PIDs (4242,
# 999999, and elsewhere 1 and 7) that may well exist on the host — PID 1
# certainly does. Without this, a unit test's outcome would depend on the
# machine's process table, and a "stale PID" case could quietly become a
# five-second poll against launchd.
def _exited(pid, timeout, **kwargs):
    return True


def _never_exits(pid, timeout, **kwargs):
    return False


_RUNNING_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    "I0101 00:00:00.000000       1 server.go:934] Created conversation {}\n"
).format(_BOUND_UUID)
_COMPLETED_LOG = _RUNNING_LOG + (
    "I0101 00:00:05.000000       1 stream.go:42] Stream completed for {}, "
    "clearing ResponsePending\n"
).format(_BOUND_UUID)
# Companion.status.build_job_row now derives "not running" (see
# STATUS_COMPLETED) from a job's `output_file` **event stream**, not from
# `_COMPLETED_LOG`'s completion-marker text alone (that mechanism moved to
# companion.status). This repo's other
# "done" job fixtures below pass `output_text=_COMPLETED_OUTPUT` so
# build_job_row still reads them as finished, not running — cancel.py's own
# selection logic (STATUS_RUNNING filtering) is unchanged. A minimal,
# synthetic **result event** line, built with json.dumps against
# stream_events.py's own field names — not a claimed real trace.
_COMPLETED_OUTPUT = (
    json.dumps(
        {
            "event": "result",
            "result": {"conversation_id": _BOUND_UUID, "status": "SUCCESS", "response": "done"},
        }
    )
    + "\n"
)


class AwaitExitSeamTest(unittest.TestCase):
    """The seam itself, tested directly.

    `_await_exit` originally declared `is_alive=_is_alive` as a default
    argument, which binds the function object at def time. Patching
    `cancel._is_alive` then had no effect on it — the seam looked injectable
    and was not, and a test suite that relied on patching it silently kept
    probing the host's real process table. This cannot be caught by any test
    that merely uses invented PIDs, because on a host where those PIDs are
    absent the real probe returns the same answer the patch would have.
    """

    def test_module_level_is_alive_is_resolved_at_call_time(self):
        with mock.patch.object(cancel, "_is_alive", return_value=False) as probe:
            self.assertTrue(cancel._await_exit(999999, timeout=0.01))
        self.assertTrue(probe.called, "_await_exit ignored the patched probe")

    def test_a_still_alive_process_times_out_rather_than_reporting_exit(self):
        with mock.patch.object(cancel, "_is_alive", return_value=True):
            self.assertFalse(cancel._await_exit(999999, timeout=0.01))

    def test_an_explicit_is_alive_argument_still_wins(self):
        calls = []

        def never_alive(pid):
            calls.append(pid)
            return False

        self.assertTrue(cancel._await_exit(4242, timeout=0.01, is_alive=never_alive))
        self.assertEqual(calls, [4242])


class _StateDirCase(unittest.TestCase):
    def setUp(self):
        self._plugin_data = tempfile.TemporaryDirectory()
        self.addCleanup(self._plugin_data.cleanup)
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        os.environ[state.PLUGIN_DATA_ENV] = self._plugin_data.name
        self.addCleanup(self._restore_env)

        self._repo_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_dir.cleanup)
        self.repo_root = str(Path(self._repo_dir.name) / "my-repo")
        Path(self.repo_root).mkdir()

        # No test in this file may probe the host's real process table. These
        # tests use invented PIDs — 1, 2, 7, 4242, 999999 — and whether such a
        # process exists is a property of the machine, not of the code under
        # test. PID 7 is a kernel thread on a Linux CI runner and absent on
        # macOS, which is exactly how a green local suite went red in CI: the
        # real liveness probe found pid 7 alive, cancel spent its full 5s
        # grace, failed to SIGKILL a process it does not own, and returned 1.
        #
        # Patched here rather than pinned at each call site because pinning
        # them one by one is what let this slip: the bulk edit keyed on
        # `job["id"]` and silently skipped the call that passes None. Tests
        # that care about liveness still inject await_exit explicitly.
        alive_patch = mock.patch.object(cancel, "_is_alive", return_value=False)
        alive_patch.start()
        self.addCleanup(alive_patch.stop)

    def _restore_env(self):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def _write_job(self, job_id, log_text, output_text=None, **patch):
        log_file = state.resolve_job_log_file(self.repo_root, job_id)
        log_file.write_text(log_text, encoding="utf-8")
        record = {"id": job_id, "kind": "review", "status": "running", "log_file": str(log_file)}
        if output_text is not None:
            output_file = state.resolve_job_output_file(self.repo_root, job_id)
            output_file.write_text(output_text, encoding="utf-8")
            record["output_file"] = str(output_file)
        record.update(patch)
        state.upsert_job(self.repo_root, record)
        return record


class TerminateSeamTest(unittest.TestCase):
    """`_terminate`'s own stale-PID handling — no real process involved,
    `os.killpg`/`os.kill` are monkeypatched to raise ESRCH directly."""

    def test_stale_pid_process_group_and_process_both_gone_does_not_raise(self):
        def raise_esrch(*args, **kwargs):
            raise ProcessLookupError()

        with mock.patch.object(cancel.os, "killpg", side_effect=raise_esrch), \
                mock.patch.object(cancel.os, "kill", side_effect=raise_esrch):
            # Must not raise — a process already gone is success, not a crash.
            cancel._terminate(999999)

    def test_process_group_signal_delivered_does_not_fall_back_to_single_process(self):
        calls = []

        def record_killpg(pid, sig):
            calls.append(("killpg", pid, sig))

        def record_kill(pid, sig):
            calls.append(("kill", pid, sig))
            raise AssertionError("should not fall back once the group signal succeeded")

        with mock.patch.object(cancel.os, "killpg", side_effect=record_killpg), \
                mock.patch.object(cancel.os, "kill", side_effect=record_kill):
            cancel._terminate(4242)

        self.assertEqual(calls, [("killpg", 4242, cancel.signal.SIGTERM)])


class CancelJobEdgeCasesTest(_StateDirCase):
    """Stale-PID and already-terminal edge cases, per the Method: these
    come before the general running-job happy path."""

    def test_cancelling_an_already_completed_job_is_a_clean_noop(self):
        job = self._write_job("job-done", _COMPLETED_LOG, output_text=_COMPLETED_OUTPUT)
        calls = []
        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: calls.append(pid),
            await_exit=_exited,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])
        self.assertIn(job["id"], message)
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(jobs[0]["status"], "running")  # untouched — no-op

    def test_cancelling_an_already_cancelled_job_is_a_clean_noop(self):
        job = self._write_job("job-cancelled", _RUNNING_LOG, status="cancelled")
        calls = []
        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: calls.append(pid),
            await_exit=_exited,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])
        self.assertIn(job["id"], message)

    def test_stale_pid_via_injected_terminate_is_still_reported_as_cancelled(self):
        """The cancel seam itself treats a terminate() call that raises
        nothing (because _terminate already swallowed ESRCH) as success —
        the job still transitions to cancelled."""
        job = self._write_job("job-stale", _RUNNING_LOG, pid=999999)
        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: None, await_exit=_exited
        )

        self.assertEqual(exit_code, 0)
        self.assertIn(job["id"], message)
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(jobs[0]["status"], STATUS_CANCELLED)

    def test_missing_pid_still_cancels_without_crashing(self):
        job = self._write_job("job-nopid", _RUNNING_LOG, pid=None)
        calls = []
        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: calls.append(pid),
            await_exit=_exited,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])  # never signalled — no PID to signal
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(jobs[0]["status"], STATUS_CANCELLED)


class CancelJobRaceWithLateResultEventTest(_StateDirCase):
    """The race the entry-time check alone cannot see: a job reads as
    `running` when cancel_job starts (its output_file has no result event
    yet), but the process finishes and writes its result event during the
    SIGTERM/SIGKILL grace window, then exits. Since `build_job_row` always
    re-reads `output_file` fresh, `cancel_job` must re-verify immediately
    before each of its three `cancelled` writes rather than trusting the
    stale entry-time `row`."""

    def test_a_result_event_landing_during_the_grace_window_is_not_overwritten_with_cancelled(self):
        job = self._write_job("job-race", _RUNNING_LOG, pid=4242, output_text="")
        output_path = Path(job["output_file"])

        def await_exit_with_late_result(pid, timeout, **kwargs):
            # Simulate the process finishing mid-grace-window: it lands its
            # result event in output_file, then exits — both observed by
            # cancel_job only once await_exit returns.
            output_path.write_text(_COMPLETED_OUTPUT, encoding="utf-8")
            return True

        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: None,
            await_exit=await_exit_with_late_result,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("completed", message.lower())
        jobs = state.list_jobs(self.repo_root)
        self.assertNotEqual(
            jobs[0]["status"], STATUS_CANCELLED,
            "a result event landed during the grace window but cancel overwrote it with cancelled",
        )

    def test_a_result_event_landing_during_the_sigkill_escalation_window_is_not_overwritten(self):
        job = self._write_job("job-race-sigkill", _RUNNING_LOG, pid=4242, output_text="")
        output_path = Path(job["output_file"])
        attempts = {"n": 0}

        def await_exit(pid, timeout, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return False  # ignores SIGTERM, escalate to SIGKILL
            # Lands its result event only once SIGKILL forces it out.
            output_path.write_text(_COMPLETED_OUTPUT, encoding="utf-8")
            return True

        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: None,
            await_exit=await_exit, force=lambda pid, sig: None,
        )

        self.assertEqual(exit_code, 0)
        jobs = state.list_jobs(self.repo_root)
        self.assertNotEqual(
            jobs[0]["status"], STATUS_CANCELLED,
            "a result event landed during SIGKILL escalation but cancel overwrote it with cancelled",
        )

class CancelJobHappyPathTest(_StateDirCase):
    def test_cancelling_a_running_job_terminates_by_pid_and_transitions_to_cancelled(self):
        job = self._write_job("job-running", _RUNNING_LOG, pid=4242)
        calls = []
        exit_code, message = cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: calls.append(pid),
            await_exit=_exited,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [4242])
        self.assertIn(job["id"], message)
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(jobs[0]["status"], STATUS_CANCELLED)

    def test_a_process_that_ignores_sigterm_is_escalated_to_sigkill(self):
        """SIGTERM is a request. If it is ignored, cancel must escalate rather
        than report a success it never verified."""
        job = self._write_job("job-stubborn", _RUNNING_LOG, pid=4242)
        forced = []
        attempts = {"n": 0}

        def await_exit(pid, timeout, **kwargs):
            attempts["n"] += 1
            return attempts["n"] > 1  # survives SIGTERM, dies on SIGKILL

        exit_code, message = cancel.cancel_job(
            self.repo_root,
            job["id"],
            terminate=lambda pid: None,
            await_exit=await_exit,
            force=lambda pid, sig: forced.append((pid, sig)),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0][0], 4242)
        self.assertEqual(forced[0][1], signal.SIGKILL)
        self.assertIn("killed", message.lower())
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(jobs[0]["status"], STATUS_CANCELLED)

    def test_a_process_surviving_sigkill_is_an_error_and_stays_running(self):
        """The case that matters most. `cancelled` short-circuits status
        rendering, so marking an undead process cancelled would hide a job
        that is still running and still spending quota. It must stay
        running and the command must fail loudly."""
        job = self._write_job("job-undead", _RUNNING_LOG, pid=4242)
        exit_code, message = cancel.cancel_job(
            self.repo_root,
            job["id"],
            terminate=lambda pid: None,
            await_exit=_never_exits,
            force=lambda pid, sig: None,
        )

        self.assertNotEqual(exit_code, 0)
        self.assertIn("4242", message)
        self.assertIn("still running", message.lower())
        jobs = state.list_jobs(self.repo_root)
        self.assertEqual(
            jobs[0]["status"], "running",
            "an undead job was marked cancelled — /agy:status will never show it again",
        )

    def test_status_agy_status_shows_a_cancelled_job_as_cancelled_not_running(self):
        """/agy:cancel's write must actually flip what /agy:status renders,
        not just the raw stored field."""
        from companion.status import build_job_row

        job = self._write_job("job-e2e", _RUNNING_LOG, pid=4242)
        cancel.cancel_job(
            self.repo_root, job["id"], terminate=lambda pid: None, await_exit=_exited
        )

        jobs = state.list_jobs(self.repo_root)
        row = build_job_row(jobs[0])
        self.assertEqual(row["status"], STATUS_CANCELLED)
        self.assertNotEqual(row["status"], STATUS_RUNNING)

    def test_unknown_job_id_is_a_clear_error_not_a_crash(self):
        exit_code, message = cancel.cancel_job(self.repo_root, "no-such-job")
        self.assertNotEqual(exit_code, 0)
        self.assertIn("no-such-job", message)

    def test_ambiguous_job_id_prefix_is_a_clear_error(self):
        self._write_job("job-abc111", _RUNNING_LOG, pid=1)
        self._write_job("job-abc222", _RUNNING_LOG, pid=2)
        exit_code, message = cancel.cancel_job(self.repo_root, "job-abc")
        self.assertNotEqual(exit_code, 0)
        self.assertIn("ambiguous", message.lower())

    def test_no_job_id_with_no_active_jobs_is_a_clear_error(self):
        self._write_job("job-done", _COMPLETED_LOG, output_text=_COMPLETED_OUTPUT)
        exit_code, message = cancel.cancel_job(self.repo_root, None)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("no active", message.lower())

    def test_no_job_id_selects_the_single_active_job(self):
        job = self._write_job("job-only-active", _RUNNING_LOG, pid=7)
        self._write_job("job-done", _COMPLETED_LOG, output_text=_COMPLETED_OUTPUT)
        calls = []
        exit_code, message = cancel.cancel_job(
            self.repo_root, None, terminate=lambda pid: calls.append(pid),
            await_exit=_exited,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [7])
        self.assertIn(job["id"], message)


class CancelCliWiringTest(unittest.TestCase):
    """One subprocess smoke test proving `agy_companion.py cancel [job-id]`
    is wired end to end through argparse — the underlying selection/
    termination logic itself is covered above."""

    def test_cancel_subcommand_with_no_active_jobs_exits_nonzero_with_a_clear_message(self):
        had_env = state.PLUGIN_DATA_ENV in os.environ
        old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "repo"
                repo.mkdir()
                subprocess.run(
                    ["git", "init", "--quiet"], cwd=str(repo), check=True, timeout=10
                )
                plugin_data = Path(tmp) / "plugin-data"
                plugin_data.mkdir()
                env = dict(os.environ)
                env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)

                cancel_proc = subprocess.run(
                    [sys.executable, str(COMPANION), "cancel"],
                    cwd=str(repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(cancel_proc.returncode, 0)
                self.assertIn("no active", cancel_proc.stdout.lower())
        finally:
            if had_env:
                os.environ[state.PLUGIN_DATA_ENV] = old_env
            else:
                os.environ.pop(state.PLUGIN_DATA_ENV, None)


if __name__ == "__main__":
    unittest.main()
