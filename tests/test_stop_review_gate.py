import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import state  # noqa: E402
from companion import setup as setup_module  # noqa: E402
from hooks import stop_review_gate  # noqa: E402


class StopReviewGateSetupToggleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_plugin_data = os.environ.get(state.PLUGIN_DATA_ENV)
        os.environ[state.PLUGIN_DATA_ENV] = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_plugin_data is not None:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_plugin_data
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def test_enable_and_disable_review_gate_in_setup(self):
        with tempfile.TemporaryDirectory() as repo:
            repo_path = Path(repo)
            # Initialize a git repo
            subprocess.run(["git", "init"], cwd=str(repo_path), check=True, stdout=subprocess.DEVNULL)

            # Initially disabled by default
            config = state.get_config(str(repo_path))
            self.assertFalse(config.get("stop_review_gate"))

            # Enable review gate
            args = mock.Mock(
                repo=str(repo_path),
                enable_review_gate=True,
                disable_review_gate=False,
                json=True,
            )
            with mock.patch("shutil.which", return_value=None):
                setup_module.run(args)

            config = state.get_config(str(repo_path))
            self.assertTrue(config.get("stop_review_gate"))

            # Disable review gate
            args = mock.Mock(
                repo=str(repo_path),
                enable_review_gate=False,
                disable_review_gate=True,
                json=True,
            )
            with mock.patch("shutil.which", return_value=None):
                setup_module.run(args)

            config = state.get_config(str(repo_path))
            self.assertFalse(config.get("stop_review_gate"))


class StopReviewGateHookLogicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_plugin_data = os.environ.get(state.PLUGIN_DATA_ENV)
        os.environ[state.PLUGIN_DATA_ENV] = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_plugin_data is not None:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_plugin_data
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def _init_repo(self):
        repo_dir = tempfile.mkdtemp(dir=self._tmp.name)
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo_dir, check=True, stdout=subprocess.DEVNULL
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_dir, check=True, stdout=subprocess.DEVNULL
        )
        return repo_dir

    def test_gate_disabled_allows_immediately_without_subprocess(self):
        repo_dir = self._init_repo()
        # Create uncommitted file
        (Path(repo_dir) / "foo.txt").write_text("hello", encoding="utf-8")

        payload = {"cwd": repo_dir, "session_id": "sess-123"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                with mock.patch.object(stop_review_gate, "run_stop_review") as mock_rev:
                    with mock.patch.object(stop_review_gate, "_is_git_dirty") as mock_dirty:
                        stop_review_gate.main()
                        self.assertEqual(mock_rev.call_count, 0)
                        self.assertEqual(mock_dirty.call_count, 0)
        res = json.loads(out.getvalue())
        self.assertEqual(res["decision"], "approve")

    def test_gate_enabled_but_git_clean_allows_without_review(self):
        repo_dir = self._init_repo()
        state.set_config(repo_dir, "stop_review_gate", True)

        payload = {"cwd": repo_dir, "session_id": "sess-123"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                with mock.patch.object(stop_review_gate, "run_stop_review") as mock_rev:
                    stop_review_gate.main()
                    self.assertEqual(mock_rev.call_count, 0)
        res = json.loads(out.getvalue())
        self.assertEqual(res["decision"], "approve")

    def test_continuation_active_allows_immediately(self):
        repo_dir = self._init_repo()
        state.set_config(repo_dir, "stop_review_gate", True)
        (Path(repo_dir) / "dirty.txt").write_text("dirty", encoding="utf-8")

        payload = {"cwd": repo_dir, "session_id": "sess-123", "continuation_active": True}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                with mock.patch.object(stop_review_gate, "run_stop_review") as mock_rev:
                    stop_review_gate.main()
                    self.assertEqual(mock_rev.call_count, 0)
        res = json.loads(out.getvalue())
        self.assertEqual(res["decision"], "approve")

    def test_dirty_repo_blocks_when_agy_reports_block(self):
        repo_dir = self._init_repo()
        state.set_config(repo_dir, "stop_review_gate", True)
        (Path(repo_dir) / "dirty.txt").write_text("dirty", encoding="utf-8")

        payload = {"cwd": repo_dir, "session_id": "sess-456"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                with mock.patch.object(
                    stop_review_gate, "run_stop_review", return_value=(False, "found syntax error")
                ):
                    stop_review_gate.main()
        res = json.loads(out.getvalue())
        self.assertEqual(res["decision"], "block")
        self.assertIn("found syntax error", res["reason"])

    def test_circuit_breaker_allows_on_second_attempt_in_same_turn(self):
        repo_dir = self._init_repo()
        state.set_config(repo_dir, "stop_review_gate", True)
        (Path(repo_dir) / "dirty.txt").write_text("dirty", encoding="utf-8")

        payload = {"cwd": repo_dir, "session_id": "sess-breaker"}

        # First run: blocked
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out1:
                with mock.patch.object(
                    stop_review_gate, "run_stop_review", return_value=(False, "issue")
                ):
                    stop_review_gate.main()
        res1 = json.loads(out1.getvalue())
        self.assertEqual(res1["decision"], "block")

        # Second run with same session_id: circuit breaker trips, allows stop without running review!
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out2:
                with mock.patch.object(stop_review_gate, "run_stop_review") as mock_rev:
                    stop_review_gate.main()
                    self.assertEqual(mock_rev.call_count, 0)
        res2 = json.loads(out2.getvalue())
        self.assertEqual(res2["decision"], "approve")

    def test_run_stop_review_parsing(self):
        repo_dir = self._init_repo()

        # ALLOW case
        mock_proc_allow = mock.Mock(returncode=0, stdout="ALLOW: clean changes\nExtra info", stderr="")
        with mock.patch("subprocess.run", return_value=mock_proc_allow):
            allow, reason = stop_review_gate.run_stop_review(repo_dir, {})
            self.assertTrue(allow)
            self.assertIsNone(reason)

        # BLOCK case
        mock_proc_block = mock.Mock(returncode=0, stdout="BLOCK: broken contract\nDetails here", stderr="")
        with mock.patch("subprocess.run", return_value=mock_proc_block):
            allow, reason = stop_review_gate.run_stop_review(repo_dir, {})
            self.assertFalse(allow)
            self.assertIn("broken contract", reason)

        # Fail open on error
        mock_proc_fail = mock.Mock(returncode=1, stdout="", stderr="crash")
        with mock.patch("subprocess.run", return_value=mock_proc_fail):
            allow, reason = stop_review_gate.run_stop_review(repo_dir, {})
            self.assertTrue(allow)

        # Fail open on timeout
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["agy"], timeout=20)):
            allow, reason = stop_review_gate.run_stop_review(repo_dir, {})
            self.assertTrue(allow)


if __name__ == "__main__":
    unittest.main()
