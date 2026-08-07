"""Tests for the `/agy:review --background` **background launch**.

Two layers, per the contract's method:
  - `BackgroundLaunchSeamTest` calls `companion.review._run_background_review`
    directly with a stub `spawn` — no process is ever started, no
    `--log-file` is ever written by anything. This proves the state-dir
    write / job-record shape / non-blocking behavior in isolation, fast.
  - `BackgroundLaunchSubprocessTest` drives the full CLI
    (`agy_companion.py review --background`) as a subprocess against the
    extended fake `agy` (`review_background_finished` behavior, which
    writes a **completion marker** and exits immediately) — proving the
    seam is wired correctly end to end, without ever spawning a
    long-lived process.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import review, state  # noqa: E402

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


def _wait_for_marker(log_path, needle, timeout=2.0, interval=0.02):
    """The launched agy process is genuinely detached from the companion
    process our subprocess.run() waited on — its own exit (however fast)
    is not synchronized with the companion returning. Poll briefly rather
    than assume the write already landed."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                return text
        time.sleep(interval)
    return text


def _dirty_repo(tmp, env):
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, env, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, env, "add", "file.txt")
    _git(repo, env, "commit", "--quiet", "-m", "seed")
    (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
    return repo


class BackgroundLaunchSeamTest(unittest.TestCase):
    """No subprocess, no real agy — `spawn` is a stub the test controls."""

    def setUp(self):
        self._plugin_data = tempfile.TemporaryDirectory()
        self.addCleanup(self._plugin_data.cleanup)
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        os.environ[state.PLUGIN_DATA_ENV] = self._plugin_data.name
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def test_launch_writes_a_running_job_record_without_waiting_on_the_spawned_command(self):
        calls = []

        class _FakeProc:
            pid = 4242

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append((cmd, cwd, stdout_path))
            return _FakeProc()  # never awaited — the seam does not block on it.

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            exit_code = review._run_background_review(
                str(repo_root), "review this diff", spawn=stub_spawn
            )
            jobs = state.list_jobs(str(repo_root))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        cmd, cwd, stdout_path = calls[0]
        # cwd is the job's **agent workspace**, never the repo: agy resolves a
        # workspace-scoped agent only from its own cwd, so launching in the
        # repo silently falls back to the default agent. The repo is attached
        # with --add-dir instead, so nothing is written into it.
        self.assertEqual(
            cwd, str(state.resolve_job_workspace(str(repo_root), jobs[0]["id"]))
        )
        self.assertNotEqual(cwd, str(repo_root))
        self.assertIn("--add-dir", cmd)
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(repo_root))
        self.assertIn("agy", cmd)
        self.assertIn("--log-file", cmd)
        self.assertIn("--agent", cmd)
        self.assertIn("agy-review", cmd)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["kind"], "review")
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["conversation"])
        self.assertEqual(job["pid"], 4242)
        log_file_arg = cmd[cmd.index("--log-file") + 1]
        self.assertEqual(job["log_file"], log_file_arg)
        # The job's stdout must be captured to a persistent output file (the
        # detached agy's stdout is the stored **result** — without this it
        # is lost to DEVNULL for good), passed through the spawn seam.
        self.assertEqual(job["output_file"], stdout_path)
        self.assertIsNotNone(stdout_path)
        # The job's log file must live under this repo's state dir, never
        # an ephemeral temp path (that is the foreground path's job).
        self.assertIn(str(state.resolve_state_dir(str(repo_root))), job["log_file"])
        self.assertIn(str(state.resolve_state_dir(str(repo_root))), job["output_file"])

    def test_agent_workspace_is_staged_and_outlives_the_launching_process(self):
        """The detached job's cwd must still hold the agent after this
        process returns. A TemporaryDirectory here would be deleted on
        return, pulling the agent out from under a job that just started —
        which the job would experience as a silent fallback, not an error."""
        calls = []

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append((cmd, cwd, stdout_path))
            return None

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            review._run_background_review(str(repo_root), "review this diff", spawn=stub_spawn)
            jobs = state.list_jobs(str(repo_root))

        # Deliberately asserted after the repo's temp dir is gone: the
        # workspace lives under the state dir, keyed by job id, so it is
        # unaffected by the launching context disappearing.
        workspace = Path(calls[0][1])
        staged = workspace / ".agents" / "agents" / "agy-review" / "agent.md"
        self.assertTrue(staged.is_file(), "agent not staged at {}".format(staged))
        self.assertEqual(
            workspace, state.resolve_job_workspace(str(repo_root), jobs[0]["id"])
        )

    def test_adversarial_background_job_stages_its_own_agent(self):
        calls = []

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append((cmd, cwd, stdout_path))
            return None

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            review._run_background_review(
                str(repo_root),
                "adversarially review this diff",
                spawn=stub_spawn,
                agent_name="agy-adversarial-review",
            )

        workspace = Path(calls[0][1])
        self.assertTrue(
            (workspace / ".agents" / "agents" / "agy-adversarial-review" / "agent.md").is_file()
        )

    def test_missing_agy_binary_is_a_clean_error_and_writes_no_job_record(self):
        def stub_spawn(cmd, cwd, stdout_path=None):
            raise FileNotFoundError("agy")

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            exit_code = review._run_background_review(
                str(repo_root), "review this diff", spawn=stub_spawn
            )
            jobs = state.list_jobs(str(repo_root))

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(jobs, [])

    def test_two_launches_get_distinct_job_ids_and_both_are_recorded(self):
        def stub_spawn(cmd, cwd, stdout_path=None):
            return None

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            review._run_background_review(str(repo_root), "prompt one", spawn=stub_spawn)
            review._run_background_review(str(repo_root), "prompt two", spawn=stub_spawn)
            jobs = state.list_jobs(str(repo_root))

        self.assertEqual(len(jobs), 2)
        self.assertNotEqual(jobs[0]["id"], jobs[1]["id"])

    def test_spawn_returning_none_records_no_pid_without_crashing(self):
        """The old stub shape (returns None, no .pid attribute) must still
        work — a stub that mimics a real Popen is not required."""

        def stub_spawn(cmd, cwd, stdout_path=None):
            return None

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            exit_code = review._run_background_review(
                str(repo_root), "review this diff", spawn=stub_spawn
            )
            jobs = state.list_jobs(str(repo_root))

        self.assertEqual(exit_code, 0)
        self.assertIsNone(jobs[0]["pid"])


class BackgroundLaunchSubprocessTest(unittest.TestCase):
    """Drives the real CLI end to end against the extended fake agy — the
    only test in this file that spawns any process, and it exits fast."""

    def test_background_review_returns_immediately_and_prints_a_job_id(self):
        had_env = state.PLUGIN_DATA_ENV in os.environ
        old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = dict(os.environ)
                env.update(_GIT_ENV)
                repo = _dirty_repo(tmp, env)
                bin_dir = Path(tmp) / "bin"
                bin_dir.mkdir()
                _install_fake_agy(bin_dir)
                plugin_data = Path(tmp) / "plugin-data"
                plugin_data.mkdir()

                env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
                env["FAKE_AGY_BEHAVIOR"] = "review_background_finished"
                env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)

                result = subprocess.run(
                    [sys.executable, str(COMPANION), "review", "--background"],
                    cwd=str(repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("job", result.stdout.lower())

                # state.list_jobs reads $CLAUDE_PLUGIN_DATA from THIS
                # process's environment, not the subprocess's — scope it
                # here to the same plugin-data dir the subprocess used.
                os.environ[state.PLUGIN_DATA_ENV] = str(plugin_data)
                jobs = state.list_jobs(str(repo))
                self.assertEqual(len(jobs), 1)
                job = jobs[0]
                self.assertEqual(job["kind"], "review")
                self.assertIn(job["id"], result.stdout)

                # The companion process returned without waiting on agy, but
                # the fake agy exits immediately anyway — by the time we get
                # here its persistent log is on disk with the completion
                # marker, proving the log-file wiring is correct end to end.
                log_text = _wait_for_marker(Path(job["log_file"]), "clearing ResponsePending")
                self.assertIn("Stream completed for", log_text)
                self.assertIn("clearing ResponsePending", log_text)
        finally:
            if had_env:
                os.environ[state.PLUGIN_DATA_ENV] = old_env
            else:
                os.environ.pop(state.PLUGIN_DATA_ENV, None)


if __name__ == "__main__":
    unittest.main()
