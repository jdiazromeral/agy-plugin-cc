"""Behavior tests for the foreground `/agy:review` live path (invoked
without --dry-run): launches `agy` once as a blocking foreground run,
verifies the run actually **bound** the `agy-review` agent from its
`--log-file` before trusting stdout, then renders the **tolerant parse**.

Driven through the companion's public interface as a subprocess, with a
tightly scoped PATH carrying only the extended fake agy (mirroring
test_setup.py's PATH scoping) — the real agy binary is never reachable from
any test in this file.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
FAKE_AGY_SOURCE = Path(__file__).resolve().parent / "fake_agy.py"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "review"

_PYTHON_DIR = str(Path(sys.executable).resolve().parent)
# The review live path shells out to `git` (target resolution,
# sizing, diff text) in addition to `agy` — the scoped PATH below must carry
# a real git so those calls succeed, alongside the fake agy and nothing else
# that could resolve to a real agy binary.
_GIT_DIR = str(Path(shutil.which("git")).resolve().parent) if shutil.which("git") else ""

# Fixed synthetic identity/dates, same rationale as test_review.py —
# reproducible commits, immune to the developer machine's git config.
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


def _repo_env():
    env = dict(os.environ)
    env.update(_GIT_ENV)
    return env


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args), cwd=str(repo), env=_repo_env(), capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError("git {} failed: {}".format(" ".join(args), result.stderr))
    return result.stdout


def _dirty_repo(tmp):
    """A repo with one committed file and one unstaged edit — a non-empty
    working-tree review target."""
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "seed")
    (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
    return repo


def _run_review_live(repo, bin_dir, behavior, extra_env=None):
    env = _repo_env()
    env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
    env["FAKE_AGY_BEHAVIOR"] = behavior
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(COMPANION), "review"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class BoundValidReviewTest(unittest.TestCase):
    def test_bound_run_renders_p0_finding_and_verdict_from_real_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)
            fixture = FIXTURES_DIR / "2026-07-24-run3.stdout.txt"

            result = _run_review_live(
                repo,
                bin_dir,
                "review_bound_valid",
                extra_env={"FAKE_AGY_STDOUT_PATH": str(fixture)},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("P0", result.stdout)
        self.assertIn("patch is incorrect", result.stdout)

    def test_bound_run_with_relative_absolute_file_path_fixture_still_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)
            fixture = FIXTURES_DIR / "2026-07-24-run4.stdout.txt"

            result = _run_review_live(
                repo,
                bin_dir,
                "review_bound_valid",
                extra_env={"FAKE_AGY_STDOUT_PATH": str(fixture)},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("calc.py", result.stdout)
        self.assertIn("patch is incorrect", result.stdout)


class SilentFallbackReviewTest(unittest.TestCase):
    def test_fallback_run_is_surfaced_as_execution_error_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_review_live(repo, bin_dir, "review_silent_fallback")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("fallback", result.stderr.lower())
        # The unrelated stdout the fallback run produced must never be
        # rendered as though it were a review.
        self.assertNotIn("patch is", result.stdout)


class UnknownBindReviewTest(unittest.TestCase):
    """An empty `--log-file` — no fallback line, no `Created conversation`
    line, no `printmode.go` line at all — is zero evidence either way.
    `_bind_check` must report "unknown", and the live path must fail
    closed exactly as it does for a confirmed silent fallback: agy's
    stdout (which still looks like a clean review) must never be rendered
    on unconfirmed binding."""

    def test_unknown_bind_is_surfaced_as_execution_error_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_review_live(repo, bin_dir, "review_unknown_bind")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("unconfirmed", result.stderr.lower())
        # The unrelated stdout the unknown-bind run produced must never be
        # rendered as though it were a review — this is the exact defect
        # class the mission fixes: "unknown" silently reported as "bound".
        self.assertNotIn("patch is", result.stdout)


class BoundButCrashedReviewTest(unittest.TestCase):
    def test_crashed_run_is_surfaced_as_execution_error_distinct_from_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_review_live(repo, bin_dir, "review_bound_crashed")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("No findings", result.stdout)
        self.assertIn("error", result.stderr.lower())


class StreamParseFallbackReviewTest(unittest.TestCase):
    """Parsing the **event stream** must never raise and never silently
    lose output: a stream that fails to parse at all, or one that never
    reaches a **result event** (e.g. a mid-stream crash), falls back to
    letting `tolerant_parse` see the raw stdout text instead of crashing or
    rendering nothing."""

    def test_unparseable_stream_falls_back_to_raw_stdout_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_review_live(repo, bin_dir, "review_stream_garbage")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        # tolerant_parse finds no JSON object in the garbage text, so
        # render_review's fallback renders it back verbatim rather than
        # crashing or silently dropping it.
        self.assertIn("not an event stream at all, just garbage text", result.stdout)

    def test_stream_with_no_result_event_falls_back_to_raw_stdout_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_review_live(repo, bin_dir, "review_stream_no_result")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        # No result event ever arrived, so response is None; the fallback
        # renders the raw init/step_update NDJSON verbatim rather than
        # crashing or rendering an empty "No findings." review.
        self.assertIn('"event": "init"', result.stdout)
        self.assertNotIn("No findings.", result.stdout)


class NoAgyOnPathTest(unittest.TestCase):
    def test_missing_agy_binary_is_a_clean_error_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            empty_bin = Path(tmp) / "empty"
            empty_bin.mkdir()

            env = _repo_env()
            # git must still be reachable (target resolution/sizing need
            # it), but no directory on this PATH contains an `agy`.
            env["PATH"] = os.pathsep.join([str(empty_bin), _GIT_DIR])
            result = subprocess.run(
                [sys.executable, str(COMPANION), "review"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("agy", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
