"""Tests for companion.state — the per-repository **state dir** under
$CLAUDE_PLUGIN_DATA, and the **job** record round-trip stored there.

Pure functions over a temp filesystem — no subprocess, no agy, no network.
Every test patches $CLAUDE_PLUGIN_DATA (or removes it) via a scoped
os.environ edit so no test ever touches the real developer machine's
plugin data directory.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import state  # noqa: E402


class ScopedEnvTestCase(unittest.TestCase):
    """Base class that scopes $CLAUDE_PLUGIN_DATA to a fresh temp dir (or
    removes it) for the duration of each test, restoring whatever was there
    before — never leaks into the real environment."""

    def setUp(self):
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def set_plugin_data(self):
        os.environ[state.PLUGIN_DATA_ENV] = self._tmp.name

    def unset_plugin_data(self):
        os.environ.pop(state.PLUGIN_DATA_ENV, None)


class StateDirKeyingTest(ScopedEnvTestCase):
    def test_state_dir_is_under_plugin_data_state_and_keyed_by_slug_and_hash(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            state_dir = state.resolve_state_dir(str(repo_root))

        self.assertEqual(state_dir.parent, Path(self._tmp.name) / "state")
        name = state_dir.name
        slug, _, digest = name.rpartition("-")
        self.assertEqual(slug, "my-repo")
        self.assertRegex(digest, r"^[0-9a-f]{16}$")

    def test_slug_is_filesystem_safe_for_a_dirty_repo_name(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "weird name!@# (copy)"
            repo_root.mkdir()
            state_dir = state.resolve_state_dir(str(repo_root))

        slug = state_dir.name.rsplit("-", 1)[0]
        self.assertRegex(slug, r"^[A-Za-z0-9._-]+$")

    def test_same_repo_root_resolves_to_the_same_state_dir_deterministically(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            first = state.resolve_state_dir(str(repo_root))
            second = state.resolve_state_dir(str(repo_root))

        self.assertEqual(first, second)

    def test_two_different_repo_roots_with_the_same_basename_get_distinct_hashes(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as base:
            repo_a = Path(base) / "a" / "repo"
            repo_b = Path(base) / "b" / "repo"
            repo_a.mkdir(parents=True)
            repo_b.mkdir(parents=True)
            dir_a = state.resolve_state_dir(str(repo_a))
            dir_b = state.resolve_state_dir(str(repo_b))

        self.assertNotEqual(dir_a, dir_b)
        self.assertTrue(dir_a.name.startswith("repo-"))
        self.assertTrue(dir_b.name.startswith("repo-"))

    def test_unset_plugin_data_env_falls_back_to_a_deterministic_temp_dir(self):
        self.unset_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            first = state.resolve_state_dir(str(repo_root))
            second = state.resolve_state_dir(str(repo_root))

        self.assertEqual(first, second)
        self.assertNotIn(str(self._tmp.name), str(first))
        self.assertIn("agy-companion", str(first))


class JobRecordRoundTripTest(ScopedEnvTestCase):
    def test_upsert_job_inserts_a_new_job_with_created_and_updated_timestamps(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            state.upsert_job(repo, {
                "id": "job-1",
                "kind": "review",
                "status": "running",
                "log_file": "/tmp/does-not-matter.log",
            })
            jobs = state.list_jobs(repo)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["id"], "job-1")
        self.assertEqual(job["kind"], "review")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["log_file"], "/tmp/does-not-matter.log")
        self.assertIsNone(job.get("conversation"))
        self.assertIn("created_at", job)
        self.assertIn("updated_at", job)

    def test_upsert_job_merges_a_patch_into_an_existing_job_and_bumps_updated_at(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            state.upsert_job(repo, {
                "id": "job-1", "kind": "review", "status": "running",
                "log_file": "/tmp/x.log",
            })
            first = state.list_jobs(repo)[0]

            state.upsert_job(repo, {
                "id": "job-1", "status": "completed",
                "conversation": "22222222-2222-2222-2222-222222222222",
            })
            jobs = state.list_jobs(repo)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["conversation"], "22222222-2222-2222-2222-222222222222")
        # Fields not present in the patch survive the merge.
        self.assertEqual(job["kind"], "review")
        self.assertEqual(job["log_file"], "/tmp/x.log")
        self.assertEqual(job["created_at"], first["created_at"])
        self.assertGreaterEqual(job["updated_at"], first["updated_at"])

    def test_state_round_trips_through_disk_across_separate_calls(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            state.upsert_job(repo, {
                "id": "job-1", "kind": "delegate", "status": "queued",
                "log_file": "/tmp/x.log",
            })
            # A brand new call, as a later turn would make, with nothing
            # cached in memory.
            jobs = state.list_jobs(repo)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["kind"], "delegate")

    def test_generate_job_id_is_unique_across_calls(self):
        first = state.generate_job_id("review")
        second = state.generate_job_id("review")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("review-"))


class JobCapTest(ScopedEnvTestCase):
    def test_jobs_are_capped_at_50_pruning_the_oldest_by_updated_at(self):
        self.set_plugin_data()
        with tempfile.TemporaryDirectory() as repo:
            for i in range(60):
                state.upsert_job(repo, {
                    "id": "job-{}".format(i),
                    "kind": "review",
                    "status": "completed",
                    "log_file": "/tmp/{}.log".format(i),
                    # Explicit updated_at so ordering is deterministic
                    # regardless of how fast the loop runs.
                    "updated_at": "2026-01-01T00:{:02d}:00+00:00".format(i % 60),
                })
            jobs = state.list_jobs(repo)

        self.assertEqual(len(jobs), state.MAX_JOBS)
        ids = {job["id"] for job in jobs}
        # The 10 oldest (job-0..job-9) must have been pruned.
        for i in range(10):
            self.assertNotIn("job-{}".format(i), ids)
        for i in range(10, 60):
            self.assertIn("job-{}".format(i), ids)


if __name__ == "__main__":
    unittest.main()
