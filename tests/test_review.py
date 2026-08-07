"""Behavior tests for target selection and dry-run sizing in the `review`
subcommand, driven through the companion's public interface: invoke it as a
subprocess with `cwd` set to a throwaway git repository the test creates in a
temp dir, and assert on its stdout / --json output. Never touches this
repository's own git state. The `--dry-run` tests here never invoke the real
agy binary; MissingDryRunTest (the one non-dry-run test in this file)
scopes PATH so it cannot reach a real agy either — see
tests/test_review_live.py for the full foreground live-path coverage.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import review  # noqa: E402

# Fixed synthetic identity/dates so commits are reproducible and the
# suite cannot be perturbed by the developer's machine's git config.
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


def _repo_env():
    env = dict(os.environ)
    env.update(_GIT_ENV)
    return env


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(repo),
        env=_repo_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            "git {} failed: {}".format(" ".join(args), result.stderr)
        )
    return result.stdout


def _init_repo(tmp, name="repo"):
    repo = Path(tmp) / name
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    return repo


def _commit(repo, message="commit"):
    _git(repo, "commit", "--quiet", "-m", message)


def _run_review(repo, extra_args=()):
    return subprocess.run(
        [sys.executable, str(COMPANION), "review", "--dry-run"] + list(extra_args),
        cwd=str(repo),
        env=_repo_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )


class UntrackedFileOnlyTest(unittest.TestCase):
    """The first TDD slice: a throwaway repo with exactly one untracked
    file resolves a working-tree target with a non-zero file count, even
    though `git diff --shortstat` would be empty for it."""

    def test_untracked_only_resolves_working_tree_with_nonzero_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "new_file.txt").write_text("hello\n", encoding="utf-8")

            result = _run_review(repo, ["--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "working-tree")
        self.assertFalse(payload["nothing_to_review"])
        self.assertEqual(payload["size"]["files"], 1)


class StagedOnlyTest(unittest.TestCase):
    def test_scope_staged_sizes_only_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "tracked.txt")
            _commit(repo, "seed")

            (repo / "staged.txt").write_text("two\n", encoding="utf-8")
            _git(repo, "add", "staged.txt")
            (repo / "unstaged.txt").write_text("three\n", encoding="utf-8")  # untracked, ignored

            result = _run_review(repo, ["--scope", "staged", "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "staged")
        self.assertTrue(payload["target"]["explicit"])
        self.assertEqual(payload["size"]["files"], 1)
        self.assertEqual(payload["size"]["insertions"], 1)
        self.assertFalse(payload["nothing_to_review"])

    def test_scope_staged_with_empty_index_reports_nothing_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "tracked.txt")
            _commit(repo, "seed")
            (repo / "unstaged.txt").write_text("edit\n", encoding="utf-8")  # untracked only

            result = _run_review(repo, ["--scope", "staged", "--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "staged")
        self.assertTrue(payload["nothing_to_review"])
        self.assertEqual(payload["size"]["files"], 0)


class MixedWorkingTreeTest(unittest.TestCase):
    def test_mixed_staged_unstaged_and_untracked_counts_unique_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "a.txt").write_text("a1\n", encoding="utf-8")
            (repo / "b.txt").write_text("b1\n", encoding="utf-8")
            _git(repo, "add", "a.txt", "b.txt")
            _commit(repo, "seed")

            # staged change
            (repo / "a.txt").write_text("a1\na2\n", encoding="utf-8")
            _git(repo, "add", "a.txt")
            # unstaged change on a different tracked file
            (repo / "b.txt").write_text("b1\nb2\n", encoding="utf-8")
            # untracked new file
            (repo / "c.txt").write_text("c1\n", encoding="utf-8")

            result = _run_review(repo, ["--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "working-tree")
        self.assertFalse(payload["target"]["explicit"])  # auto-selected because dirty
        self.assertEqual(payload["size"]["files"], 3)
        self.assertEqual(payload["size"]["insertions"], 2)


class BranchVsBaseTest(unittest.TestCase):
    def test_base_flag_diffs_against_explicit_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "file.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "seed")

            _git(repo, "checkout", "--quiet", "-b", "feature")
            (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "feature work")

            result = _run_review(repo, ["--base", "main", "--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "branch")
        self.assertEqual(payload["target"]["base_ref"], "main")
        self.assertTrue(payload["target"]["explicit"])
        self.assertEqual(payload["size"]["files"], 1)
        self.assertFalse(payload["nothing_to_review"])

    def test_scope_branch_detects_default_branch_without_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "file.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "seed")

            _git(repo, "checkout", "--quiet", "-b", "feature")
            (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "feature work")

            result = _run_review(repo, ["--scope", "branch", "--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "branch")
        self.assertEqual(payload["target"]["base_ref"], "main")
        self.assertEqual(payload["size"]["files"], 1)


class CleanNothingToReviewTest(unittest.TestCase):
    def test_clean_tree_with_no_commits_ahead_of_base_reports_nothing_to_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "file.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "seed")

            result = _run_review(repo, ["--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["mode"], "branch")
        self.assertTrue(payload["nothing_to_review"])
        self.assertEqual(payload["size"]["files"], 0)

    def test_human_output_distinguishes_nothing_to_review_from_resolved_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "file.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "seed")

            nothing_result = _run_review(repo)

            (repo / "dirty.txt").write_text("new\n", encoding="utf-8")
            resolved_result = _run_review(repo)

        self.assertIn("Nothing to review", nothing_result.stdout)
        self.assertNotIn("Nothing to review", resolved_result.stdout)
        self.assertIn("Review target:", resolved_result.stdout)


class NotAGitRepositoryTest(unittest.TestCase):
    def test_outside_a_git_repo_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_review(Path(tmp), ["--json"])

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("git repository", result.stderr)


class UnsupportedScopeTest(unittest.TestCase):
    def test_unsupported_scope_value_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            result = _run_review(repo, ["--scope", "bogus"])

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)


class MissingDryRunTest(unittest.TestCase):
    """Without --dry-run, `review` now runs the M4 foreground live path
    (launches agy via subprocess) rather than the old "only supports
    --dry-run" stub error. This test must never be able to reach a REAL agy
    binary — it scopes PATH down to git plus an empty directory, so the live
    launch deterministically hits the clean "agy not found" error, proving
    the live path really did try to launch agy (not some other failure)
    without ever risking a real invocation."""

    def test_review_without_dry_run_is_a_clean_error_not_a_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "file.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "file.txt")
            _commit(repo, "seed")
            (repo / "dirty.txt").write_text("new\n", encoding="utf-8")

            empty_bin = Path(tmp) / "empty"
            empty_bin.mkdir()
            git_dir = str(Path(shutil.which("git")).resolve().parent)
            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(empty_bin), git_dir])

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


class AgyCommandVectorRequestsEventStreamTest(unittest.TestCase):
    """`_agy_command` is the one place both the foreground and background
    launch paths build their command vector — both must inherit
    `--output-format stream-json` from this single change. `--json-schema`
    is never added anywhere in this mission's diff, per the epic's settled
    verdict (docs/json-schema-verdict.md)."""

    def test_command_vector_requests_stream_json_output_format(self):
        cmd = review._agy_command("prompt text", "/tmp/agy.log")

        self.assertIn("--output-format", cmd)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--json-schema", cmd)

    def test_command_vector_always_disables_slash_command_expansion(self):
        """`prompt` is verbatim user text (originating from `/agy:review`'s
        target selection and, via adversarial_review.py's free-text focus,
        `/agy:adversarial-review`), so a leading `/` must never be resolved
        as a **slash-command expansion**, which agy resolves in print mode
        from 1.1.9 on. Unconditional — no version check, since every
        supported binary is above that."""
        cmd = review._agy_command("prompt text", "/tmp/agy.log")
        self.assertIn("--disable-slash-commands", cmd)


class PromptTextTest(unittest.TestCase):
    def test_prompt_no_longer_carries_the_m2_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "new_file.txt").write_text("hello\n", encoding="utf-8")

            result = _run_review(repo, ["--json"])
            # The human report presents the same prompt text.
            human_result = _run_review(repo)

        payload = json.loads(result.stdout)
        self.assertNotIn("PLACEHOLDER", payload["prompt"])
        self.assertIn("Output only the JSON", payload["prompt"])
        self.assertIn("Prompt that would be sent to agy", human_result.stdout)


if __name__ == "__main__":
    unittest.main()
