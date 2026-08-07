"""Tests for the `--repo <path>` passthrough shared by every repo-scoped
subcommand — `delegate`, `review`, `adversarial-review`, `status`, `result`
and `cancel`.

Before this flag each of those resolved its repository from the process
**cwd** (`ensure_git_repository(".")`, and review's `cwd = "."`), so
invoking the companion from anywhere that is not itself a git repo — a
multi-repo workspace root, say — failed with "This command must run inside
a git repository" and offered no way to name the repo actually meant. The
launch itself was never the problem: `agy` has always run with
`cwd=repo_root` and the **state dir** has always been keyed on
`realpath(repo_root)`, so naming the root is the whole fix.

Covers, per the plan:
  - every repo-scoped subcommand parses `--repo`
  - `--repo` reaches the launch: `agy`'s subprocess `cwd` **and** the job's
    state dir are the *named* repo, asserted from a process cwd that is not
    a repo at all — a flag that parses and is then dropped before the
    subprocess call is exactly the defect worth guarding (mirrors
    tests/test_timeout_flag.py's reasoning)
  - `review` resolves its target against `--repo`, not cwd
  - omitting `--repo` still resolves the process cwd (regression)
  - a `--repo` that does not exist, and one that exists but is not a repo,
    each fail with a message naming the path — never the misleading "git is
    not installed" that a nonexistent `cwd=` produces through `_run_git`'s
    `FileNotFoundError` arm

Per AGENTS.md's Method this module owns its own small helpers rather than
importing a sibling test module's.
"""
import argparse
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

from companion import (  # noqa: E402
    adversarial_review,
    cancel,
    delegate,
    result,
    review,
    state,
    status,
)

# Every subcommand that resolves a repository, with the minimal argv that
# parses for it. `setup` is deliberately absent: it checks the agy install,
# not a repo.
_REPO_SCOPED = (
    ("delegate", delegate, ["do a task"]),
    ("review", review, []),
    ("adversarial-review", adversarial_review, []),
    ("status", status, []),
    ("result", result, []),
    ("cancel", cancel, []),
)

_GIT_ENV = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": "/nonexistent",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}


def _parse_args(module, argv):
    parser = argparse.ArgumentParser()
    module.add_arguments(parser)
    return parser.parse_args(argv)


def _git(repo, *args):
    result_ = subprocess.run(
        ["git"] + list(args), cwd=str(repo), env=_GIT_ENV,
        capture_output=True, text=True, timeout=10,
    )
    if result_.returncode != 0:
        raise AssertionError("git {} failed: {}".format(" ".join(args), result_.stderr))
    return result_.stdout


def _dirty_repo(parent):
    """A repo with one commit and an uncommitted change, so review's `auto`
    scope resolves to a non-empty working-tree diff."""
    repo = Path(parent) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "seed")
    (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
    return repo


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


class _OutsideAnyRepo:
    """Runs the body with the process cwd set to a temp directory that is
    NOT a git repository — the situation the flag exists for. Restores the
    original cwd on exit."""

    def __enter__(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        return Path(self._tmp.name)

    def __exit__(self, *exc_info):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()


# --- the flag exists on every repo-scoped subcommand ----------------------


class RepoFlagParsesEverywhereTest(unittest.TestCase):
    def test_every_repo_scoped_subcommand_accepts_repo(self):
        for name, module, argv in _REPO_SCOPED:
            with self.subTest(subcommand=name):
                args = _parse_args(module, argv + ["--repo", "/some/path"])
                self.assertEqual(args.repo, "/some/path")

    def test_repo_defaults_to_none_so_cwd_still_wins(self):
        for name, module, argv in _REPO_SCOPED:
            with self.subTest(subcommand=name):
                self.assertIsNone(_parse_args(module, argv).repo)


# --- --repo reaches the launch, not just the parser -----------------------


class DelegateRepoFlagTest(unittest.TestCase):
    """`--repo` must reach `subprocess.run`'s `cwd=` — that kwarg is what
    bounds the sandboxed agy run's writes — and the job record must land in
    the named repo's state dir, all from a cwd that is not a repo.

    `ensure_git_repository` is NOT stubbed here (it is half of what is under
    test), so the fake must intercept only the `agy` launch and let the git
    subprocesses through — `delegate.subprocess` and `review.subprocess` are
    the same module object, so a blanket patch swallows both (AGENTS.md,
    "Patch the imported name in each module's own namespace")."""

    @staticmethod
    def _agy_only(calls, real_run=subprocess.run):
        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "agy":
                calls.append(kwargs)
                return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")
            return real_run(cmd, **kwargs)
        return fake_run

    def test_named_repo_becomes_the_agy_cwd_and_the_state_dir(self):
        calls = []
        fake_run = self._agy_only(calls)

        with tempfile.TemporaryDirectory() as parent, _ScopedPluginData():
            repo = _dirty_repo(parent)
            with _OutsideAnyRepo():
                args = _parse_args(delegate, ["do a task", "--repo", str(repo)])
                with mock.patch.object(delegate.subprocess, "run", side_effect=fake_run):
                    exit_code = delegate.run(args)

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                Path(calls[0]["cwd"]).resolve(), repo.resolve(),
                "agy must run in the named repo, not the process cwd",
            )
            jobs = state.list_jobs(str(repo))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["kind"], "delegate")

    def test_without_repo_the_process_cwd_still_wins(self):
        calls = []
        fake_run = self._agy_only(calls)

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as parent, _ScopedPluginData():
            repo = _dirty_repo(parent)
            os.chdir(str(repo))
            try:
                args = _parse_args(delegate, ["do a task"])
                with mock.patch.object(delegate.subprocess, "run", side_effect=fake_run):
                    exit_code = delegate.run(args)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertEqual(Path(calls[0]["cwd"]).resolve(), repo.resolve())


class ReviewRepoFlagTest(unittest.TestCase):
    """Review resolves its target with git before it ever reaches
    `ensure_git_repository`, so `--repo` has to reach target selection too.
    `--dry-run` exercises exactly that path and launches no agy."""

    def test_dry_run_resolves_the_named_repos_target_from_outside(self):
        with tempfile.TemporaryDirectory() as parent, _ScopedPluginData():
            repo = _dirty_repo(parent)
            with _OutsideAnyRepo():
                args = _parse_args(review, ["--dry-run", "--json", "--repo", str(repo)])
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = review.run(args)

        self.assertEqual(exit_code, 0, stdout.getvalue())
        self.assertIn("file.txt", stdout.getvalue())


class StatusResultCancelRepoFlagTest(unittest.TestCase):
    """The read-side commands are what a user reaches for right after a
    background launch, from whatever directory they happen to be in."""

    def _run_from_outside(self, module, argv, repo):
        stdout, stderr = io.StringIO(), io.StringIO()
        with _OutsideAnyRepo():
            args = _parse_args(module, argv + ["--repo", str(repo)])
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = module.run(args)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_status_reads_the_named_repos_jobs(self):
        with tempfile.TemporaryDirectory() as parent, _ScopedPluginData():
            repo = _dirty_repo(parent)
            state.upsert_job(str(repo), {
                "id": "delegate-abc123", "kind": "delegate", "status": "running",
            })
            exit_code, out, err = self._run_from_outside(
                status, ["--all-sessions"], repo
            )

        self.assertEqual(exit_code, 0, err)
        self.assertIn("delegate-abc123", out)

    def test_result_and_cancel_resolve_the_named_repo(self):
        """Neither has a job to act on here; what matters is that they get
        past repo resolution and report about the *job*, not about cwd."""
        with tempfile.TemporaryDirectory() as parent, _ScopedPluginData():
            repo = _dirty_repo(parent)
            for module in (result, cancel):
                with self.subTest(subcommand=module.__name__):
                    exit_code, out, err = self._run_from_outside(module, [], repo)
                    self.assertNotIn("git repository", out + err)


# --- a bad --repo fails clearly ------------------------------------------


class BadRepoArgumentTest(unittest.TestCase):
    def test_nonexistent_path_names_the_path_and_not_git(self):
        with _OutsideAnyRepo() as outside:
            missing = str(Path(outside) / "no-such-dir")
            args = _parse_args(status, ["--repo", missing])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = status.run(args)

        self.assertEqual(exit_code, 1)
        self.assertIn(missing, stderr.getvalue())
        self.assertNotIn(
            "git is not installed", stderr.getvalue(),
            "a nonexistent cwd must not be misreported as a missing git binary",
        )

    def test_directory_that_is_not_a_repo_names_the_path(self):
        with _OutsideAnyRepo() as outside:
            args = _parse_args(status, ["--repo", str(outside)])
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = status.run(args)

        self.assertEqual(exit_code, 1)
        self.assertIn(str(Path(outside).resolve()), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
