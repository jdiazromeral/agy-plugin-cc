"""Tests for the foreground `delegate` **job**'s missing **output file** and
the resulting **zombie job** (mission `companion-layering`/M1).

Today `delegate._run_live_delegate` forwards the text it reads back from
`agy` straight to `sys.stdout` and drops it — no **output file** is ever
written, and the **job** record's `state.upsert_job` call omits
`output_file` entirely. `status.build_job_row` then re-derives a fresh
**terminal status** foreground job as `running` on every call, because it
re-derives status from the **log file** + **event stream** unconditionally
except for a stored `cancelled`. A finished foreground `delegate` job is
therefore a **zombie job**: `/agy:result` reports it "not finished yet" and
`/agy:cancel`'s default target selection could pick it as if it were live.

This module proves both halves of the fix:
  - a fresh foreground `delegate` run leaves a harvestable **output file**
    and reports a **terminal status** (regression test);
  - a **job** record already on disk in the OLD shape (stored `completed`,
    no `output_file`) is read as terminal with no migration step
    (**zombie job** self-heal);
  - `/agy:cancel`'s default target selection skips a finished foreground
    `delegate` job and still finds a genuinely running background job.

Per AGENTS.md/the mission contract's Method: helpers are duplicated here
rather than imported from `tests/test_delegate.py` or `tests/test_status.py`,
and `delegate._launch_agy` is patched by name in `delegate`'s own namespace
(never a bare `subprocess.run` patch) since `delegate.py` runs git
subprocesses first.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import cancel, delegate, result, state, status  # noqa: E402

_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}

# The fake agy's actual delegate response text (tests/fake_agy.py's
# _DELEGATE_STDOUT) — reused as a literal here rather than imported, per the
# "duplicate small test helpers" rule; this module never spawns the fake agy
# binary at all, so it does not need the rest of that module.
_REPLY_TEXT = "agy delegate output: task complete.\n"

# A minimal, realistic bound-conversation log trace — enough for
# `delegate._log_conversation_if_bound` to resolve a conversation, without
# any of the fallback trace `CONVERSATION_NOT_FOUND_RE` guards against.
_BOUND_LOG_TEXT = "Created conversation 12345678-1234-1234-1234-123456789abc\n"


def _repo_env():
    env = dict(os.environ)
    env.update(_GIT_ENV)
    return env


def _git(repo, *args):
    proc = subprocess.run(
        ["git"] + list(args), cwd=str(repo), env=_repo_env(),
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise AssertionError("git {} failed: {}".format(" ".join(args), proc.stderr))
    return proc.stdout


def _init_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "seed")
    return repo


@contextlib.contextmanager
def _scoped_plugin_data():
    """Scopes $CLAUDE_PLUGIN_DATA to a fresh temp dir for the duration of
    the `with` block, restoring whatever was there before. Duplicated from
    test_delegate.py's `_ScopedPluginData` per the no-cross-import rule."""
    had_env = state.PLUGIN_DATA_ENV in os.environ
    old_env = os.environ.get(state.PLUGIN_DATA_ENV)
    with tempfile.TemporaryDirectory() as tmp:
        os.environ[state.PLUGIN_DATA_ENV] = tmp
        try:
            yield Path(tmp)
        finally:
            if had_env:
                os.environ[state.PLUGIN_DATA_ENV] = old_env
            else:
                os.environ.pop(state.PLUGIN_DATA_ENV, None)


class _FakeCompletedProcess:
    """Stand-in for `subprocess.CompletedProcess`, carrying only the
    fields `_run_live_delegate` actually reads."""

    def __init__(self, returncode, stdout, stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_launch_agy_factory(reply_text=_REPLY_TEXT, log_text=_BOUND_LOG_TEXT, returncode=0):
    """A `delegate._launch_agy`-shaped stub: never invokes the real `agy`
    (forbidden by the mission contract), just hands back a canned
    (result, log_text) pair the way a real foreground FRESH launch would —
    plain stdout text, no `--output-format stream-json` (the fresh vector
    never requests one, so its output file must hold plain text, not an
    **event stream**)."""

    def _stub(repo_root, cmd, log_file, timeout=None):
        return _FakeCompletedProcess(returncode, reply_text.encode("utf-8")), log_text

    return _stub


def _delegate_args(repo, task="write a poem"):
    return SimpleNamespace(
        repo=str(repo), task=[task], background=False, resume=False,
        model=None, effort=None, timeout=None,
    )


class ForegroundFreshRegressionTest(unittest.TestCase):
    """Drives the foreground fresh `delegate` path with `_launch_agy`
    stubbed, then proves the finished job is harvestable via
    `result.result_for_job` AND reports a **terminal status** via
    `status.build_job_row` — both assertions, not one."""

    def test_finished_foreground_delegate_job_is_harvestable_and_terminal(self):
        with tempfile.TemporaryDirectory() as tmp, _scoped_plugin_data():
            repo = _init_repo(tmp)
            args = _delegate_args(repo)

            captured_stdout = io.StringIO()
            with mock.patch.object(delegate, "_launch_agy", _stub_launch_agy_factory()):
                with contextlib.redirect_stdout(captured_stdout):
                    exit_code = delegate.run(args)

            jobs = state.list_jobs(str(repo))
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job["kind"], "delegate")
            self.assertIn("output_file", job)
            self.assertIsNotNone(job["output_file"])
            output_text = Path(job["output_file"]).read_text(encoding="utf-8")

            # Retrievable via result.result_for_job: exit 0, the run's
            # actual reply text in the message. Read here, inside the
            # scoped-plugin-data block, since output_file lives under it.
            result_exit, message = result.result_for_job(str(repo), job["id"])

            # And status.build_job_row reports a terminal status for it —
            # not merely "not running": the specific terminal status this
            # run actually reached.
            row = status.build_job_row(job)

        # Stdout is unchanged: the user still sees exactly the forwarded
        # bytes, verbatim and in full.
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured_stdout.getvalue(), _REPLY_TEXT)
        self.assertEqual(output_text, _REPLY_TEXT)
        self.assertEqual(result_exit, 0)
        self.assertEqual(message, _REPLY_TEXT)
        self.assertEqual(row["status"], status.STATUS_COMPLETED)


class ZombieJobSelfHealTest(unittest.TestCase):
    """A **job** record already on disk in the OLD shape — stored
    `completed`, `log_file` set, no `output_file` key at all — must report
    a **terminal status**, proving the fix needs no migration step."""

    def test_old_shape_completed_job_with_no_output_file_reads_as_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "old-shape.log"
            log_file.write_text(_BOUND_LOG_TEXT, encoding="utf-8")
            job = {
                "id": "delegate-zombie123",
                "kind": "delegate",
                "status": "completed",
                "log_file": str(log_file),
                "conversation": "12345678-1234-1234-1234-123456789abc",
                "session_id": None,
            }
            # No "output_file" key at all — the exact old shape.
            self.assertNotIn("output_file", job)

            row = status.build_job_row(job)

        self.assertEqual(row["status"], status.STATUS_COMPLETED)
        self.assertNotEqual(row["status"], status.STATUS_RUNNING)


class CancelBlastRadiusTest(unittest.TestCase):
    """`/agy:cancel`'s default target selection (`cancel._select_job`) must
    not pick a finished foreground `delegate` job, and must still pick a
    genuinely running background job when one is present."""

    def test_select_job_skips_finished_delegate_and_picks_running_background_job(self):
        finished_delegate = {
            "id": "delegate-finished01",
            "kind": "delegate",
            "status": "completed",
            "log_file": "/nonexistent/finished-delegate.log",
            "conversation": None,
            "session_id": None,
        }
        running_background = {
            "id": "review-running01",
            "kind": "review",
            "status": "running",
            "log_file": "/nonexistent/running-review.log",
            "conversation": None,
            "session_id": None,
        }
        jobs = [finished_delegate, running_background]

        selected, error = cancel._select_job("/irrelevant/repo", jobs, None)

        self.assertIsNone(error)
        self.assertEqual(selected["id"], "review-running01")


if __name__ == "__main__":
    unittest.main()
