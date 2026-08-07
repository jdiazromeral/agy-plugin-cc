"""Cross-command integration test for M8: proves the six shipped commands
genuinely interoperate through the shared per-repo **state dir**.

Driven entirely through the fake `agy` + the companion CLI — every step is a
`subprocess.run([sys.executable, COMPANION, <subcommand>, ...])` call, never
a direct call into `companion.*` internals to fake the interop. A background
`review` **job** and a background `delegate` job are launched into the same
repo's state dir; `/agy:status` must show both (one `completed`, one
`running`); `/agy:result` must **harvest** the finished job's stored
**result**; `/agy:cancel` must cancel the running one, and a subsequent
`/agy:status` must reflect it as `cancelled`.

Fully offline and deterministic: no wall-clock races. Detached agy processes
are genuinely detached from the companion process our subprocess.run() waits
on, so their writes are not synchronized with the companion returning — this
reuses the polling-for-the-detached-write pattern
`tests/test_review_background.py` and `tests/test_delegate.py` already
established (poll for a log needle) rather than sleeping a fixed amount.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
FAKE_AGY_SOURCE = Path(__file__).resolve().parent / "fake_agy.py"

_PYTHON_DIR = str(Path(sys.executable).resolve().parent)
_GIT_DIR = str(Path(shutil.which("git")).resolve().parent) if shutil.which("git") else ""

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

# Matches the job id + log path out of review.py's / delegate.py's
# "Launched background <kind> job <id> (log: <path>). ..." stdout line —
# the only thing this test ever reads to learn a job's identity, mirroring
# how a real user would.
_LAUNCH_LINE_RE = re.compile(r"Launched background \w+ job (\S+) \(log: (.+)\)\.")


def _install_fake_agy(bin_dir):
    body = FAKE_AGY_SOURCE.read_text(encoding="utf-8")
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    dest = bin_dir / "agy"
    dest.write_text("#!{}\n".format(sys.executable) + "".join(lines), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def _git(repo, env, *args):
    result = subprocess.run(
        ["git"] + list(args), cwd=str(repo), env=env, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError("git {} failed: {}".format(" ".join(args), result.stderr))
    return result.stdout


def _dirty_repo(tmp, env):
    """A repo with an uncommitted change, so /agy:review --background's
    `auto` scope resolves to a non-empty working-tree diff instead of
    printing "Nothing to review" and exiting without launching anything."""
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, env, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "file.txt")
    _git(repo, env, "commit", "--quiet", "-m", "seed")
    (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
    return repo


def _wait_for_marker(log_path, needle, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                return text
        time.sleep(interval)
    return text


class CrossCommandIntegrationTest(unittest.TestCase):
    """One repo, one state dir, all six commands driven as subprocesses in
    sequence: delegate --background, review --background, status, result,
    cancel, status again."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(COMPANION)] + list(args),
            cwd=str(self.repo),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _launch_and_extract(self, *args):
        result = self._run(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        match = _LAUNCH_LINE_RE.search(result.stdout)
        self.assertIsNotNone(match, "no job id found in: {!r}".format(result.stdout))
        job_id, log_path = match.group(1), match.group(2)
        return job_id, Path(log_path)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name

        base_env = dict(os.environ)
        base_env.update(_GIT_ENV)
        # A stray session-scoping var in the ambient environment would make
        # /agy:status filter to just that session by default — this test
        # wants both jobs visible with no session scoping in play.
        base_env.pop("AGY_COMPANION_SESSION_ID", None)

        self.repo = _dirty_repo(tmp, base_env)

        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        _install_fake_agy(bin_dir)

        plugin_data = Path(tmp) / "plugin-data"
        plugin_data.mkdir()

        self.env = dict(base_env)
        self.env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
        self.env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)

    def test_review_and_delegate_jobs_interoperate_through_the_shared_state_dir(self):
        # --- launch a delegate job that finishes ---------------------------
        delegate_env = dict(self.env)
        delegate_env["FAKE_AGY_BEHAVIOR"] = "delegate_background_finished"
        delegate_result = subprocess.run(
            [sys.executable, str(COMPANION), "delegate", "--background", "do the task"],
            cwd=str(self.repo), env=delegate_env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(delegate_result.returncode, 0, delegate_result.stderr)
        d_match = _LAUNCH_LINE_RE.search(delegate_result.stdout)
        self.assertIsNotNone(d_match)
        delegate_job_id, delegate_log = d_match.group(1), Path(d_match.group(2))
        # Poll for the detached write to land — the launch subprocess
        # returns without waiting on the (already-fast) detached agy.
        delegate_log_text = _wait_for_marker(delegate_log, "clearing ResponsePending")
        self.assertIn("Stream completed for", delegate_log_text)

        # --- launch a review job that stays running -------------------------
        review_env = dict(self.env)
        # "review_bound_valid" writes a bound log AND a full event stream
        # (init + result, SUCCESS) now that review._agy_command
        # always request --output-format stream-json — so it models a job
        # that has already completed, not one still running. "review_stream_no_result"
        # writes the same bound log but a genuinely truncated event stream
        # (init + step_update, no result event), which is what a still-running
        # background review actually looks like on disk: status.derive_status
        # only calls a job "completed" once a result event with SUCCESS has
        # landed (plugins/agy/scripts/companion/status.py), so this is the
        # fixture that models "running" with the stream requested.
        review_env["FAKE_AGY_BEHAVIOR"] = "review_stream_no_result"
        review_result = subprocess.run(
            [sys.executable, str(COMPANION), "review", "--background"],
            cwd=str(self.repo), env=review_env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(review_result.returncode, 0, review_result.stderr)
        r_match = _LAUNCH_LINE_RE.search(review_result.stdout)
        self.assertIsNotNone(r_match)
        review_job_id, review_log = r_match.group(1), Path(r_match.group(2))
        # "review_stream_no_result" writes the same bound --log-file template
        # as "review_bound_valid" (a "Created conversation" line, no completion
        # marker) — poll for that, so status is read only once the write landed.
        review_log_text = _wait_for_marker(review_log, "Created conversation")
        self.assertIn("Created conversation", review_log_text)

        # --- /agy:status shows both jobs, one completed, one running --------
        status_result = self._run("status", "--json")
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        rows = {row["id"]: row for row in json.loads(status_result.stdout)}

        self.assertIn(delegate_job_id, rows)
        self.assertIn(review_job_id, rows)
        self.assertEqual(rows[delegate_job_id]["kind"], "delegate")
        self.assertEqual(rows[delegate_job_id]["status"], "completed")
        self.assertEqual(rows[review_job_id]["kind"], "review")
        self.assertEqual(rows[review_job_id]["status"], "running")

        # --- /agy:result harvests the finished delegate job's stored result -
        # This exercises the M6/M7 parallel-fork gap this mission's own
        # integration test surfaced and fixed: delegate.py's background
        # launch did not used to capture stdout to a persistent output_file
        # at all, so there was nothing for /agy:result to harvest.
        result_result = self._run("result", delegate_job_id)
        self.assertEqual(result_result.returncode, 0, result_result.stderr)
        self.assertIn("agy delegate output: task complete.", result_result.stdout)

        # A still-running job must never be rendered as a harvestable result.
        running_result_result = self._run("result", review_job_id)
        self.assertNotEqual(running_result_result.returncode, 0)
        self.assertIn("running", running_result_result.stdout.lower())

        # --- /agy:cancel cancels the running review job ----------------------
        cancel_result = self._run("cancel", review_job_id)
        self.assertEqual(cancel_result.returncode, 0, cancel_result.stderr)
        self.assertIn("Cancelled job", cancel_result.stdout)
        self.assertIn(review_job_id, cancel_result.stdout)

        # --- /agy:status now reflects the cancellation, and the completed
        # delegate job is untouched -------------------------------------------
        final_status_result = self._run("status", "--json")
        self.assertEqual(final_status_result.returncode, 0, final_status_result.stderr)
        final_rows = {row["id"]: row for row in json.loads(final_status_result.stdout)}

        self.assertEqual(final_rows[review_job_id]["status"], "cancelled")
        self.assertEqual(final_rows[delegate_job_id]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
