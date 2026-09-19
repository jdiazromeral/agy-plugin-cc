"""Unit tests for Claude Code lifecycle hooks (session_start, session_end)."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "plugins" / "agy" / "scripts" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import session_end  # noqa: E402
import session_start  # noqa: E402


class SessionStartHookTest(unittest.TestCase):
    def test_session_start_appends_env_var_to_claude_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / "claude.env"
            orig_env = os.environ.get("CLAUDE_ENV_FILE")
            try:
                os.environ["CLAUDE_ENV_FILE"] = str(env_file)
                # Simulate stdin json
                import io
                old_stdin = sys.stdin
                sys.stdin = io.StringIO(json.dumps({"session_id": "test-session-1234"}))
                try:
                    ret = session_start.main()
                finally:
                    sys.stdin = old_stdin

                self.assertEqual(ret, 0)
                content = env_file.read_text(encoding="utf-8")
                self.assertIn('export AGY_COMPANION_SESSION_ID="test-session-1234"', content)
            finally:
                if orig_env is None:
                    os.environ.pop("CLAUDE_ENV_FILE", None)
                else:
                    os.environ["CLAUDE_ENV_FILE"] = orig_env


class SessionEndHookTest(unittest.TestCase):
    def test_session_end_never_signals_a_live_process_and_never_mutates_state_json(self):
        """Replaces test_session_end_cancels_matching_running_jobs, which
        asserted the opposite, buggy behavior. Proven over GENUINELY alive
        processes (never an already-gone pid, which would prove nothing):
        SessionEnd must not signal either job's process, and must not
        rewrite state.json's `jobs` array at all — not the ending session's
        own job, and not any other job either.
        """
        target_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        other_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_dir = pathlib.Path(tmp) / "state" / "myrepo-abc123"
                state_dir.mkdir(parents=True)
                state_file = state_dir / "state.json"
                initial_state = {
                    "version": 1,
                    "jobs": [
                        {
                            "id": "job-1",
                            "session_id": "sess-target",
                            "status": "running",
                            "pid": target_proc.pid,
                        },
                        {
                            "id": "job-2",
                            "session_id": "sess-other",
                            "status": "running",
                            "pid": other_proc.pid,
                        },
                    ],
                }
                original_json = json.dumps(initial_state)
                state_file.write_text(original_json, encoding="utf-8")

                orig_plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
                try:
                    os.environ["CLAUDE_PLUGIN_DATA"] = tmp
                    session_end.cleanup_session_jobs("sess-target")
                finally:
                    if orig_plugin_data is None:
                        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
                    else:
                        os.environ["CLAUDE_PLUGIN_DATA"] = orig_plugin_data

                # Both processes must still be alive — SessionEnd must never
                # signal a job's process, targeted or not.
                self.assertIsNone(
                    target_proc.poll(),
                    "BUG REPRODUCED: SessionEnd signalled the ending session's own job",
                )
                self.assertIsNone(
                    other_proc.poll(),
                    "BUG REPRODUCED: SessionEnd signalled a job outside the ending session",
                )

                # state.json must be untouched — no read-modify-write at all.
                self.assertEqual(
                    state_file.read_text(encoding="utf-8"),
                    original_json,
                    "BUG REPRODUCED: SessionEnd mutated state.json",
                )
        finally:
            target_proc.kill()
            other_proc.kill()
            target_proc.wait(timeout=5)
            other_proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
