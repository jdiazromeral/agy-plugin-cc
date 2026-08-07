"""Tests for the `--timeout` passthrough shared by `delegate`, `review`, and
(through review's shared launch path) `adversarial-review` — mission
timeout-ceiling/M1. The foreground `agy` timeout was a fixed 300s baked into
`_AGY_TIMEOUT_SECONDS` in both `delegate.py` and `review.py`, with no way to
raise it at the call site; `--timeout <seconds>` is the escape hatch.

Covers, per the mission contract:
  - `--timeout N` reaches `subprocess.run`'s `timeout=` kwarg for both
    `delegate` and `review` (and, through review's launch path,
    `adversarial-review`) — asserted on the captured kwarg itself, not on
    `args.timeout`, since a flag that parses and then gets dropped before
    reaching the subprocess call is exactly the defect worth guarding.
  - The default stays 300 when `--timeout` is absent.
  - A non-positive or non-integer `--timeout` is rejected by argparse with a
    clear error, never silently coerced.
  - `--timeout` combined with `--background` is a loud error (the
    background path has no ceiling by design; accepting the flag there
    would be a silent no-op).

Also covers `companion.launch`'s `--print-timeout` derivation (mission
agy-1111-free-probes/M-print-timeout): agy's own print-mode wait timeout
defaults to `5m0s` — exactly `_AGY_TIMEOUT_SECONDS` — so left unset it races
the plugin's own subprocess timeout for the same deadline. Covered here:
  - `launch._print_timeout_arg`, the pure helper, in isolation (normal
    case, the small-`--timeout` floor, and a regression test that the
    emitted value always carries a unit — a bare integer is an agy exit-2
    crash).
  - Every foreground vector (`delegate`, `review`, and through review's
    launch path, `adversarial-review`) carries a `--print-timeout` strictly
    less than the subprocess `timeout=` it runs under, at both the default
    and a custom `--timeout`.
  - Every background vector carries the large, explicit
    `launch._BACKGROUND_PRINT_TIMEOUT_ARG`, never agy's silent 5-minute
    default.

Per AGENTS.md's Method, this module owns its own small helpers rather than
importing tests.test_delegate's/tests.test_review's.
"""
import argparse
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import adversarial_review, delegate, launch, review, state  # noqa: E402

_STUB_TARGET = {
    "mode": "working-tree",
    "label": "working tree diff",
    "base_ref": None,
    "explicit": True,
}
_STUB_SIZE = {"files": 1, "insertions": 1, "deletions": 0}
_STUB_REVIEW_JSON = b'{"findings": [], "overall_correctness": "PATCH_IS_CORRECT"}\n'


def _parse_args(module, argv):
    parser = argparse.ArgumentParser()
    module.add_arguments(parser)
    return parser.parse_args(argv)


def _bound_log_text(uuid):
    return "I0101 00:00:00.000000       1 server.go:1007] Created conversation {}\n".format(uuid)


class _ScopedPluginData:
    """Scopes $CLAUDE_PLUGIN_DATA to a fresh temp dir for the duration of a
    `with` block, restoring whatever was there before. Mirrors
    tests/test_delegate.py's helper of the same name."""

    def __enter__(self):
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ[state.PLUGIN_DATA_ENV] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc_info):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)
        self._tmp.cleanup()


# --- --timeout N really reaches subprocess.run's timeout= kwarg -----------


class DelegateTimeoutReachesSubprocessTest(unittest.TestCase):
    """`_launch_agy`'s `subprocess.run` call is the one true consumer of the
    timeout value. `ensure_git_repository` is stubbed so no real git
    process runs; `subprocess.run` itself is stubbed so no real `agy` runs
    either — only the kwargs it was called with matter here."""

    def _run_and_capture(self, argv):
        args = _parse_args(delegate, argv)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root, _ScopedPluginData():
            with mock.patch.object(delegate, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(delegate.subprocess, "run", side_effect=fake_run):
                exit_code = delegate.run(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_explicit_timeout_reaches_the_subprocess_call(self):
        kwargs = self._run_and_capture(["do a task", "--timeout", "45"])
        self.assertEqual(kwargs["timeout"], 45)

    def test_absent_timeout_defaults_to_300_at_the_subprocess_call(self):
        kwargs = self._run_and_capture(["do a task"])
        self.assertEqual(kwargs["timeout"], 300)
        self.assertEqual(kwargs["timeout"], delegate._AGY_TIMEOUT_SECONDS)


class ReviewTimeoutReachesSubprocessTest(unittest.TestCase):
    """Same proof as above, for `review`'s foreground launch. Target
    selection (`resolve_target`/`target_size`/`_diff_text`/`_tracked_files`)
    is stubbed so no real git process runs; the stubbed `subprocess.run`
    also writes a bound `--log-file` so the run completes as a genuine
    success rather than tripping the unrelated bind check."""

    def _run_and_capture(self, argv):
        args = _parse_args(review, argv)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs)
            log_file = cmd[cmd.index("--log-file") + 1]
            Path(log_file).write_text(
                _bound_log_text("99999999-9999-9999-9999-999999999999"), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0, stdout=_STUB_REVIEW_JSON, stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root:
            with mock.patch.object(review, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(review, "resolve_target", return_value=dict(_STUB_TARGET)), \
                    mock.patch.object(review, "target_size", return_value=dict(_STUB_SIZE)), \
                    mock.patch.object(review, "_diff_text", return_value="diff --git a/x b/x\n"), \
                    mock.patch.object(review, "_tracked_files", return_value=["x"]), \
                    mock.patch.object(review.subprocess, "run", side_effect=fake_run):
                exit_code = review.run(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_explicit_timeout_reaches_the_subprocess_call(self):
        kwargs = self._run_and_capture(["--timeout", "45"])
        self.assertEqual(kwargs["timeout"], 45)

    def test_absent_timeout_defaults_to_300_at_the_subprocess_call(self):
        kwargs = self._run_and_capture([])
        self.assertEqual(kwargs["timeout"], 300)
        self.assertEqual(kwargs["timeout"], review._AGY_TIMEOUT_SECONDS)


class AdversarialReviewTimeoutReachesSubprocessTest(unittest.TestCase):
    """`adversarial-review` gains `--timeout` through review's shared
    `_run_live_review`/`_launch_agy` launch path, not a fork of it — proved
    here the same way as `ReviewTimeoutReachesSubprocessTest`, but driven
    through `adversarial_review.run()`."""

    def test_explicit_timeout_reaches_the_subprocess_call(self):
        args = _parse_args(adversarial_review, ["--timeout", "45"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(kwargs)
            log_file = cmd[cmd.index("--log-file") + 1]
            Path(log_file).write_text(
                _bound_log_text("88888888-8888-8888-8888-888888888888"), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0, stdout=_STUB_REVIEW_JSON, stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root:
            with mock.patch.object(adversarial_review, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(adversarial_review, "resolve_target", return_value=dict(_STUB_TARGET)), \
                    mock.patch.object(adversarial_review, "target_size", return_value=dict(_STUB_SIZE)), \
                    mock.patch.object(adversarial_review, "_diff_text", return_value="diff --git a/x b/x\n"), \
                    mock.patch.object(adversarial_review, "_tracked_files", return_value=["x"]), \
                    mock.patch.object(review.subprocess, "run", side_effect=fake_run):
                exit_code = adversarial_review.run(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], 45)


# --- rejection: non-positive or non-integer --timeout ----------------------


class TimeoutRejectionTest(unittest.TestCase):
    """argparse's `type=` callback must reject a bad `--timeout` value
    outright — never coerce a float string by truncation, and never clamp a
    negative or zero value up to something positive."""

    def _expect_rejection(self, module, argv):
        parser = argparse.ArgumentParser()
        module.add_arguments(parser)
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(stderr):
            parser.parse_args(argv)
        self.assertEqual(ctx.exception.code, 2)
        return stderr.getvalue()

    def test_delegate_rejects_zero(self):
        stderr = self._expect_rejection(delegate, ["do a task", "--timeout", "0"])
        self.assertIn("--timeout", stderr)

    def test_delegate_rejects_negative(self):
        stderr = self._expect_rejection(delegate, ["do a task", "--timeout=-5"])
        self.assertIn("--timeout", stderr)

    def test_delegate_rejects_non_integer(self):
        stderr = self._expect_rejection(delegate, ["do a task", "--timeout", "5.5"])
        self.assertIn("--timeout", stderr)

    def test_review_rejects_zero(self):
        stderr = self._expect_rejection(review, ["--timeout", "0"])
        self.assertIn("--timeout", stderr)

    def test_review_rejects_non_integer(self):
        stderr = self._expect_rejection(review, ["--timeout", "soon"])
        self.assertIn("--timeout", stderr)

    def test_adversarial_review_rejects_negative(self):
        stderr = self._expect_rejection(adversarial_review, ["--timeout=-1"])
        self.assertIn("--timeout", stderr)


# --- --timeout + --background is a loud error, not a silent no-op ---------


class TimeoutBackgroundConflictTest(unittest.TestCase):
    """The background path has no timeout ceiling by design; accepting
    `--timeout` there would be a silent no-op — exactly the class of silent
    fallback AGENTS.md's Never section forbids. Each `run()` rejects the
    combination before doing anything else, so no repo/subprocess seam is
    needed here."""

    def test_delegate_rejects_timeout_with_background(self):
        args = _parse_args(delegate, ["do a task", "--timeout", "10", "--background"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = delegate.run(args)
        self.assertEqual(exit_code, 1)
        self.assertIn("--timeout", stderr.getvalue())
        self.assertIn("--background", stderr.getvalue())

    def test_review_rejects_timeout_with_background(self):
        args = _parse_args(review, ["--timeout", "10", "--background"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = review.run(args)
        self.assertEqual(exit_code, 1)
        self.assertIn("--timeout", stderr.getvalue())
        self.assertIn("--background", stderr.getvalue())

    def test_adversarial_review_rejects_timeout_with_background(self):
        args = _parse_args(adversarial_review, ["--timeout", "10", "--background"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = adversarial_review.run(args)
        self.assertEqual(exit_code, 1)
        self.assertIn("--timeout", stderr.getvalue())
        self.assertIn("--background", stderr.getvalue())


# --- launch._print_timeout_arg: the pure helper, in isolation --------------


class PrintTimeoutArgHelperTest(unittest.TestCase):
    """`launch._print_timeout_arg` is the pure, no-I/O helper mapping a
    foreground launch's subprocess timeout (whole seconds) to agy's
    `--print-timeout` argument STRING — the value that must always expire
    BEFORE the subprocess timeout, so agy always loses the race (see
    companion.launch's module docstring)."""

    def test_normal_case_subtracts_the_margin_and_appends_the_unit(self):
        self.assertEqual(launch._print_timeout_arg(300), "290s")
        self.assertEqual(launch._print_timeout_arg(45), "35s")
        self.assertEqual(launch._print_timeout_arg(60), "50s")

    def test_small_timeout_floors_at_one_second_never_negative_or_zero(self):
        """A user CAN pass `--timeout 5`; naive subtraction (5 - 10) would
        emit a negative duration. Must floor at
        `_MIN_PRINT_TIMEOUT_SECONDS` (1) instead — never negative, never
        zero."""
        self.assertEqual(launch._print_timeout_arg(5), "1s")
        self.assertEqual(launch._print_timeout_arg(1), "1s")
        self.assertEqual(launch._print_timeout_arg(10), "1s")  # exactly the margin

    def test_every_emitted_value_carries_a_unit_never_a_bare_integer(self):
        """Regression guard for the exact defect this flag exists to avoid:
        agy 1.1.11 parses `--print-timeout` as a Go duration and a bare
        integer is a hard exit-2 crash (`missing unit in duration "290"`),
        never a graceful fallback — see AGENTS.md "Settled findings"."""
        for seconds in (1, 5, 10, 11, 45, 300, 10_000):
            value = launch._print_timeout_arg(seconds)
            self.assertRegex(
                value, r"^\d+s$",
                "no bare-integer regression for input {}: got {!r}".format(seconds, value),
            )


# --- foreground vectors: --print-timeout strictly less than the subprocess
# timeout, at both the default and a custom --timeout ------------------------


class ForegroundVectorPrintTimeoutTest(unittest.TestCase):
    """Every foreground launch vector (delegate, review, and through
    review's shared launch path, adversarial-review) must carry a
    `--print-timeout` whose numeric value is strictly less than the
    subprocess `timeout=` the SAME launch runs under — never equal (that
    was today's undefined-race bug), never greater."""

    def _assert_print_timeout_precedes(self, cmd, subprocess_timeout):
        self.assertIn("--print-timeout", cmd)
        value = cmd[cmd.index("--print-timeout") + 1]
        self.assertRegex(value, r"^\d+s$")
        self.assertLess(int(value[:-1]), subprocess_timeout)
        return value

    def test_delegate_default_timeout(self):
        args = _parse_args(delegate, ["do a task"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root, _ScopedPluginData():
            with mock.patch.object(delegate, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(delegate.subprocess, "run", side_effect=fake_run):
                delegate.run(args)

        value = self._assert_print_timeout_precedes(calls[0], delegate._AGY_TIMEOUT_SECONDS)
        self.assertEqual(value, "290s")

    def test_delegate_custom_timeout_moves_the_derived_value_with_it(self):
        args = _parse_args(delegate, ["do a task", "--timeout", "45"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root, _ScopedPluginData():
            with mock.patch.object(delegate, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(delegate.subprocess, "run", side_effect=fake_run):
                delegate.run(args)

        value = self._assert_print_timeout_precedes(calls[0], 45)
        self.assertEqual(value, "35s")

    def _run_review_and_capture_cmd(self, module, argv, uuid):
        args = _parse_args(module, argv)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            log_file = cmd[cmd.index("--log-file") + 1]
            Path(log_file).write_text(_bound_log_text(uuid), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout=_STUB_REVIEW_JSON, stderr=b"")

        with tempfile.TemporaryDirectory() as repo_root:
            with mock.patch.object(module, "ensure_git_repository", return_value=repo_root), \
                    mock.patch.object(module, "resolve_target", return_value=dict(_STUB_TARGET)), \
                    mock.patch.object(module, "target_size", return_value=dict(_STUB_SIZE)), \
                    mock.patch.object(module, "_diff_text", return_value="diff --git a/x b/x\n"), \
                    mock.patch.object(module, "_tracked_files", return_value=["x"]), \
                    mock.patch.object(review.subprocess, "run", side_effect=fake_run):
                module.run(args)
        return calls[0]

    def test_review_default_timeout(self):
        cmd = self._run_review_and_capture_cmd(
            review, [], "99999999-9999-9999-9999-999999999999"
        )
        value = self._assert_print_timeout_precedes(cmd, review._AGY_TIMEOUT_SECONDS)
        self.assertEqual(value, "290s")

    def test_review_custom_timeout_moves_the_derived_value_with_it(self):
        cmd = self._run_review_and_capture_cmd(
            review, ["--timeout", "45"], "99999999-9999-9999-9999-999999999999"
        )
        value = self._assert_print_timeout_precedes(cmd, 45)
        self.assertEqual(value, "35s")

    def test_adversarial_review_custom_timeout(self):
        """adversarial-review gains --print-timeout through review's shared
        `_run_live_review`/`_launch_agy` launch path, not a fork of it —
        proved through `adversarial_review.run()` the same way
        `AdversarialReviewTimeoutReachesSubprocessTest` proves --timeout
        itself does."""
        cmd = self._run_review_and_capture_cmd(
            adversarial_review, ["--timeout", "45"], "88888888-8888-8888-8888-888888888888"
        )
        value = self._assert_print_timeout_precedes(cmd, 45)
        self.assertEqual(value, "35s")


# --- background vectors: the large explicit value, never agy's 5m default --


class BackgroundVectorPrintTimeoutTest(unittest.TestCase):
    """A --background launch is documented as having no timeout ceiling by
    design; agy's own --print-timeout defaults to 5m0s regardless, so every
    background vector must carry the large, explicit
    `launch._BACKGROUND_PRINT_TIMEOUT_ARG` instead of leaving the flag
    unset (which would silently reimpose that 5-minute ceiling)."""

    def test_delegate_background_command_vector(self):
        cmd = delegate._background_agy_command("do the task", "/tmp/x.log")
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(
            cmd[cmd.index("--print-timeout") + 1], launch._BACKGROUND_PRINT_TIMEOUT_ARG
        )
        self.assertEqual(launch._BACKGROUND_PRINT_TIMEOUT_ARG, "24h")

    def test_review_background_command_vector(self):
        cmd = review._agy_command(
            "prompt", "/tmp/agy.log", print_timeout=launch._BACKGROUND_PRINT_TIMEOUT_ARG
        )
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(
            cmd[cmd.index("--print-timeout") + 1], launch._BACKGROUND_PRINT_TIMEOUT_ARG
        )

    def test_delegate_background_launch_seam_carries_it(self):
        """Through `_run_background_delegate`'s spawn seam — no subprocess,
        a stub `spawn` per the established pattern (tests/test_delegate.py's
        `DelegateLaunchSeamTest`)."""
        calls = []

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append(cmd)
            return None

        with tempfile.TemporaryDirectory() as repo, _ScopedPluginData():
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            delegate._run_background_delegate(
                str(repo_root), "do the task", None, None, None, spawn=stub_spawn
            )

        cmd = calls[0]
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(cmd[cmd.index("--print-timeout") + 1], "24h")

    def test_review_background_launch_seam_carries_it(self):
        """Through `_run_background_review`'s spawn seam, mirroring
        tests/test_review_background.py's `BackgroundLaunchSeamTest`."""
        calls = []

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append(cmd)
            return None

        with tempfile.TemporaryDirectory() as repo, _ScopedPluginData():
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            review._run_background_review(str(repo_root), "review this diff", spawn=stub_spawn)

        cmd = calls[0]
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(cmd[cmd.index("--print-timeout") + 1], "24h")


if __name__ == "__main__":
    unittest.main()
