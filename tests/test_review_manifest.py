"""Behavior tests for `assemble_prompt`'s **manifest** seam.

A diff shows what CHANGED, never what EXISTS — the vendored review agents
(`agy-review`, `agy-adversarial-review`) have no file-access tools and are
handed only a diff, so they cannot tell whether a file is merely outside the
diff or genuinely absent. The **manifest** (the repo's tracked files, per
`git ls-files`) closes that gap: spliced into the prompt as context for
existence checks, never as material to review.

Unit-level throughout: calls `companion.review.assemble_prompt` and
`companion.review._tracked_files` directly. `DryRunIncludesManifestTest` is
the one integration-style case, driving the real `review` subcommand's
`--dry-run` path through a throwaway git repo to prove the manifest is
actually wired into the live prompt, not just reachable in isolation.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import review  # noqa: E402

_TARGET = {
    "mode": "working-tree",
    "label": "working tree diff",
    "base_ref": None,
    "explicit": False,
}
_SIZE = {"files": 1, "insertions": 1, "deletions": 0}

# Fixed synthetic identity/dates, mirroring tests/test_review.py —
# so the one integration test in this file stays reproducible.
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
    import os
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
        raise AssertionError("git {} failed: {}".format(" ".join(args), result.stderr))
    return result.stdout


def _init_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    return repo


class ManifestAbsentByDefaultTest(unittest.TestCase):
    """Every existing caller of `assemble_prompt` (both commands' `run()`,
    and every test predating this mission) calls it with no `manifest`
    keyword at all — the new parameter must default so that shape keeps
    working unchanged, and must render nothing extra when omitted."""

    def test_manifest_omitted_when_not_supplied(self):
        prompt = review.assemble_prompt(_TARGET, _SIZE)

        self.assertNotIn("Tracked files", prompt)

    def test_review_instructions_only_call_shape_still_works(self):
        prompt = review.assemble_prompt(_TARGET, _SIZE, review_instructions="do the thing")

        self.assertIn("do the thing", prompt)
        self.assertNotIn("Tracked files", prompt)

    def test_empty_manifest_list_is_also_cleanly_absent(self):
        prompt = review.assemble_prompt(_TARGET, _SIZE, manifest=[])

        self.assertNotIn("Tracked files", prompt)


class ManifestPresentWhenSuppliedTest(unittest.TestCase):
    def test_manifest_files_appear_in_the_prompt(self):
        prompt = review.assemble_prompt(_TARGET, _SIZE, manifest=["a.py", "b/c.py"])

        self.assertIn("Tracked files", prompt)
        self.assertIn("a.py", prompt)
        self.assertIn("b/c.py", prompt)

    def test_manifest_is_labelled_as_context_not_material_to_review(self):
        prompt = review.assemble_prompt(_TARGET, _SIZE, manifest=["a.py"])

        self.assertIn("existence", prompt.lower())
        self.assertIn("not itself material to review", prompt)


class ManifestTruncationTest(unittest.TestCase):
    """Truncation must be deterministic (same cap, same cutoff every call)
    and the cutoff itself must be legible in the prompt text — a reviewing
    agent must never mistake "not shown" for "does not exist"."""

    def test_truncates_past_the_cap_and_says_so_visibly(self):
        cap = review._MANIFEST_MAX_FILES
        many = ["file_{:05d}.py".format(i) for i in range(cap + 5)]

        prompt = review.assemble_prompt(_TARGET, _SIZE, manifest=many)

        # The last file within the cap is shown...
        self.assertIn(many[cap - 1], prompt)
        # ...but every file past the cap is genuinely absent from the text,
        # not merely unasserted — a truncated-but-silent list would let the
        # agent believe the manifest was exhaustive.
        for dropped in many[cap:]:
            self.assertNotIn(dropped, prompt)
        # The truncation itself, and the cap it truncated at, must be
        # visible in the prompt text, not a magic number only code knows.
        self.assertIn("truncated", prompt.lower())
        self.assertIn(str(cap), prompt)
        self.assertIn("5 more", prompt)

    def test_truncation_is_deterministic_across_calls(self):
        cap = review._MANIFEST_MAX_FILES
        many = ["file_{:05d}.py".format(i) for i in range(cap + 5)]

        first = review.assemble_prompt(_TARGET, _SIZE, manifest=many)
        second = review.assemble_prompt(_TARGET, _SIZE, manifest=many)

        self.assertEqual(first, second)


class TrackedFilesHelperTest(unittest.TestCase):
    """`_tracked_files` is the `git ls-files` seam `run()` uses to build the
    manifest it hands to `assemble_prompt` — distinct from `_diff_text` and
    `companion.git.target_size`, which both describe the CHANGE, not the repo's full
    tracked-file set."""

    def test_tracked_files_lists_committed_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "tracked.txt")
            _git(repo, "commit", "--quiet", "-m", "seed")
            (repo / "untracked.txt").write_text("two\n", encoding="utf-8")

            files = review._tracked_files(str(repo))

        self.assertIn("tracked.txt", files)
        self.assertNotIn("untracked.txt", files)


class DryRunIncludesManifestTest(unittest.TestCase):
    """End-to-end: `/agy:review --dry-run` against a real throwaway repo
    must show the manifest in the prompt it would send — proving the wiring
    from `run()` through to `assemble_prompt` actually happened, not just
    that the function supports it in isolation."""

    def test_dry_run_prompt_lists_the_repos_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            (repo / "committed.txt").write_text("v1\n", encoding="utf-8")
            _git(repo, "add", "committed.txt")
            _git(repo, "commit", "--quiet", "-m", "seed")
            (repo / "dirty.txt").write_text("new\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(COMPANION), "review", "--dry-run", "--json"],
                cwd=str(repo),
                env=_repo_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("committed.txt", result.stdout)
        self.assertIn("Tracked files", result.stdout)


if __name__ == "__main__":
    unittest.main()
