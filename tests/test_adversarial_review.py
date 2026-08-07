"""Behavior tests for `/agy:adversarial-review`: the free-text `focus`
argument reaching the assembled prompt, the correct `agy-adversarial-review`
agent name in the `agy` command vector, the **silent fallback** path
surfacing as a distinct error (not a rendered review), and `--dry-run` never
invoking `agy`.

Driven through the companion's public interface as a subprocess (mirroring
tests/test_review.py and tests/test_review_live.py), plus one direct unit
test against the reused, now agent-name-parameterized `review._agy_command`.
The real agy binary is never reachable from any test in this file.
"""
import json
import os
import re
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
AGENT_MD = REPO_ROOT / "plugins" / "agy" / "agents" / "agy-adversarial-review" / "agent.md"
AGY_REVIEW_AGENT_MD = REPO_ROOT / "plugins" / "agy" / "agents" / "agy-review" / "agent.md"
REAL_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "adversarial_review" / "2026-07-30-run1.ndjson"
)

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
JSON_KEY_RE = re.compile(r'"([A-Za-z_]+)"\s*:')


def _schema_keys(agent_md_path):
    """Extract the ordered JSON key names from an agent.md's output-schema
    block. Used to prove the two review agents' schemas stay in lockstep —
    both are parsed by the same tolerant_parse/render_review, so a
    divergence in either file silently breaks rendering for one of them."""
    text = agent_md_path.read_text(encoding="utf-8")
    block = JSON_BLOCK_RE.search(text)
    assert block is not None, "{} has no ```json output-schema block".format(agent_md_path)
    return JSON_KEY_RE.findall(block.group(1))

sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))
from companion import adversarial_review, review  # noqa: E402

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
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "seed")
    (repo / "file.txt").write_text("v1\nv2\n", encoding="utf-8")
    return repo


def _run_dry_run(repo, extra_args=()):
    return subprocess.run(
        [sys.executable, str(COMPANION), "adversarial-review", "--dry-run", "--json"] + list(extra_args),
        cwd=str(repo),
        env=_repo_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )


class FocusTextReachesPromptTest(unittest.TestCase):
    def test_free_text_focus_after_flags_appears_in_the_assembled_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            result = _run_dry_run(repo, ["--scope", "working-tree", "auth", "and", "tenant", "isolation"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("auth and tenant isolation", payload["prompt"])

    def test_no_focus_supplied_is_not_an_error_and_prompt_has_no_placeholder_leftover(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            result = _run_dry_run(repo, ["--scope", "working-tree"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotIn("User focus", payload["prompt"])
        self.assertIn("Adversarially review", payload["prompt"])


class AgentNameInCommandVectorTest(unittest.TestCase):
    """Unit-level: the reused, now agent-name-parameterized
    `review._agy_command` builds the right vector for the adversarial agent
    when called with `adversarial_review._AGY_AGENT_NAME` — no forked
    command-vector builder exists in adversarial_review.py."""

    def test_command_vector_requests_the_adversarial_agent_not_agy_review(self):
        cmd = review._agy_command(
            "prompt text", "/tmp/agy.log", agent_name=adversarial_review._AGY_AGENT_NAME
        )

        self.assertEqual(adversarial_review._AGY_AGENT_NAME, "agy-adversarial-review")
        self.assertIn("--agent", cmd)
        self.assertEqual(cmd[cmd.index("--agent") + 1], "agy-adversarial-review")
        self.assertNotIn("agy-review", cmd)
        self.assertIn("--sandbox", cmd)
        self.assertIn("--new-project", cmd)
        self.assertIn("--log-file", cmd)
        self.assertIn("--disable-slash-commands", cmd)


class SilentFallbackAdversarialReviewTest(unittest.TestCase):
    def test_fallback_run_is_surfaced_as_execution_error_naming_the_adversarial_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
            env["FAKE_AGY_BEHAVIOR"] = "review_silent_fallback"
            result = subprocess.run(
                [sys.executable, str(COMPANION), "adversarial-review"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("fallback", result.stderr.lower())
        self.assertIn("agy-adversarial-review", result.stderr)
        # The unrelated stdout the fallback run produced must never be
        # rendered as though it were a review.
        self.assertNotIn("patch is", result.stdout)


class DryRunDoesNotInvokeAgyTest(unittest.TestCase):
    def test_dry_run_succeeds_with_no_agy_on_path_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            empty_bin = Path(tmp) / "empty"
            empty_bin.mkdir()

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(empty_bin), _GIT_DIR])
            result = subprocess.run(
                [sys.executable, str(COMPANION), "adversarial-review", "--dry-run"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Prompt that would be sent to agy", result.stdout)


class BoundValidAdversarialReviewTest(unittest.TestCase):
    """End-to-end sanity: a bound, schema-valid run still renders through
    the UNCHANGED tolerant_parse/render_review path (proves the adversarial
    command really did reuse review_output.py rather than fork it)."""

    def test_bound_run_renders_finding_and_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
            env["FAKE_AGY_BEHAVIOR"] = "review_bound_valid"
            result = subprocess.run(
                [sys.executable, str(COMPANION), "adversarial-review"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("patch is correct", result.stdout)


class BoundAdversarialReviewFromRealFixtureTest(unittest.TestCase):
    """Replays `tests/fixtures/adversarial_review/2026-07-30-run1.ndjson` —
    a real, byte-exact **event stream** capture from a live, authenticated
    `agy-adversarial-review` run (see that directory's PROVENANCE.md) —
    unwrapped, verbatim, through the fake agy's stdout (via
    FAKE_AGY_STREAM_PATH, distinct from FAKE_AGY_STDOUT_PATH, which wraps
    its content instead). This is the first time the P0-P3 findings-table
    half of `review_output.render_review` has ever executed against real
    captured output — every fixture used elsewhere in this suite is either a
    hand-described schema or the one real end-to-end `/agy:review` run on
    record, which returned a clean, empty-findings verdict."""

    def test_real_capture_renders_p0_finding_row_and_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
            env["FAKE_AGY_BEHAVIOR"] = "review_bound_valid"
            env["FAKE_AGY_STREAM_PATH"] = str(REAL_FIXTURE)
            result = subprocess.run(
                [sys.executable, str(COMPANION), "adversarial-review"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        # The finding table header and a P0 row — the branch of
        # render_review this mission exists to finally exercise against
        # real output.
        self.assertIn("| Priority | Finding | Location | Confidence |", result.stdout)
        self.assertIn("| P0 |", result.stdout)
        self.assertIn("Fix incorrect subtraction operator", result.stdout)
        self.assertIn("Verdict: patch is incorrect", result.stdout)


class AdversarialAgentFileTest(unittest.TestCase):
    """Offline assertions for the vendored agy-adversarial-review agent —
    nothing here invokes the real agy binary or the network. Mirrors
    tests/test_review_agent.py's AgentFileTest for agy-review."""

    def _read(self):
        return AGENT_MD.read_text(encoding="utf-8")

    def test_agent_file_exists(self):
        self.assertTrue(AGENT_MD.is_file(), "{} does not exist".format(AGENT_MD))

    def test_opens_with_valid_yaml_frontmatter_naming_the_agent(self):
        text = self._read()
        match = FRONTMATTER_RE.match(text)
        self.assertIsNotNone(match, "agent.md must open with --- delimited frontmatter")
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name:\s*agy-adversarial-review\s*$")
        self.assertRegex(frontmatter, r"(?m)^description:\s*\S")

    def test_frontmatter_does_not_declare_a_tools_field(self):
        # A `tools:` field crashes headless `agy -p --sandbox --new-project`
        # runs (docs/review-schema-verdict.md Finding C) — same constraint
        # as agy-review's agent.md.
        text = self._read()
        match = FRONTMATTER_RE.match(text)
        frontmatter = match.group(1)
        self.assertNotRegex(frontmatter, r"(?m)^tools:")

    def test_body_has_h1_heading_delimiting_system_prompt(self):
        text = self._read()
        match = FRONTMATTER_RE.match(text)
        body = text[match.end():]
        self.assertRegex(body, r"(?m)^# .+")

    def test_provenance_header_names_upstream_origin_honestly(self):
        text = self._read()
        self.assertIn("openai/codex-plugin-cc", text)
        self.assertIn("Apache", text)
        self.assertIn("2.0", text)
        self.assertIn("prompts/adversarial-review.md", text)
        self.assertIn("commands/adversarial-review.md", text)
        # Honesty bar: the schema-divergence adaptation and the one thing
        # this mission genuinely could not re-verify are both stated.
        self.assertIn("does NOT match upstream", text)
        self.assertIn("offline", text.lower())

    def test_output_schema_matches_agy_reviews_not_upstreams(self):
        """The most important adaptation decision in this mission: the
        schema in this file must be agy-review's (line_range.start/end,
        overall_correctness "patch is correct"/"patch is incorrect"), never
        upstream's own (needs-attention/approve, line_start/line_end)."""
        text = self._read()
        self.assertIn('"line_range"', text)
        self.assertIn("patch is correct", text)
        self.assertIn("patch is incorrect", text)
        self.assertNotIn("needs-attention", text.split("PROVENANCE")[-1].split("-->", 1)[-1])
        self.assertNotIn("line_start", text.split("-->", 1)[-1])

    def test_schema_stays_in_lockstep_with_agy_review(self):
        """The guard the agent.md PROVENANCE block asks for by name: both
        review agents are parsed by the SAME tolerant_parse/render_review,
        so their output schemas must not drift apart. The sibling
        test_output_schema_matches_agy_reviews_not_upstreams asserts this
        file's shape against hardcoded markers, which would still pass if
        agy-review's own schema were edited tomorrow; this one compares the
        two files directly, so a change to EITHER breaks the build."""
        self.assertEqual(
            _schema_keys(AGENT_MD),
            _schema_keys(AGY_REVIEW_AGENT_MD),
            "agy-adversarial-review and agy-review output schemas have drifted; "
            "they share a parser, so both must be updated together",
        )

    def test_no_banned_words(self):
        text = self._read().lower()
        for word in ("broker", "app-server", "remote control"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
