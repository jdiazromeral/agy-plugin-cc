"""Unit tests for Claude Code lifecycle hooks (session_start, session_end)."""
import json
import os
import pathlib
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
    def test_session_end_cancels_matching_running_jobs(self):
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
                        "pid": 999999,  # Non-existent PID
                    },
                    {
                        "id": "job-2",
                        "session_id": "sess-other",
                        "status": "running",
                        "pid": 888888,
                    },
                ],
            }
            state_file.write_text(json.dumps(initial_state), encoding="utf-8")

            orig_plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
            try:
                os.environ["CLAUDE_PLUGIN_DATA"] = tmp
                session_end.cleanup_session_jobs("sess-target")

                updated = json.loads(state_file.read_text(encoding="utf-8"))
                jobs_by_id = {j["id"]: j for j in updated["jobs"]}
                self.assertEqual(jobs_by_id["job-1"]["status"], "cancelled")
                self.assertEqual(jobs_by_id["job-2"]["status"], "running")
            finally:
                if orig_plugin_data is None:
                    os.environ.pop("CLAUDE_PLUGIN_DATA", None)
                else:
                    os.environ["CLAUDE_PLUGIN_DATA"] = orig_plugin_data


if __name__ == "__main__":
    unittest.main()
