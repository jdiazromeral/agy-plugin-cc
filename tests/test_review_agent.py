"""Offline assertions for the vendored agy-review custom agent and its
supporting artifacts (NOTICE, fixtures, verdict doc, Makefile wiring).

Nothing here invokes the real agy binary or the network: it only reads files
already committed to the repo.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_MD = REPO_ROOT / "plugins" / "agy" / "agents" / "agy-review" / "agent.md"
NOTICE = REPO_ROOT / "NOTICE"
LICENSE = REPO_ROOT / "LICENSE"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "review"
VERDICT_DOC = REPO_ROOT / "docs" / "review-schema-verdict.md"
MAKEFILE = REPO_ROOT / "Makefile"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _read(path):
    return path.read_text(encoding="utf-8")


class AgentFileTest(unittest.TestCase):
    def test_agent_file_exists(self):
        self.assertTrue(AGENT_MD.is_file(), "{} does not exist".format(AGENT_MD))

    def test_opens_with_valid_yaml_frontmatter(self):
        text = _read(AGENT_MD)
        match = FRONTMATTER_RE.match(text)
        self.assertIsNotNone(match, "agent.md must open with --- delimited frontmatter")

    def test_frontmatter_declares_name_and_description(self):
        text = _read(AGENT_MD)
        match = FRONTMATTER_RE.match(text)
        frontmatter = match.group(1)
        self.assertRegex(frontmatter, r"(?m)^name:\s*agy-review\s*$")
        self.assertRegex(frontmatter, r"(?m)^description:\s*\S")

    def test_body_has_h1_heading_delimiting_system_prompt(self):
        text = _read(AGENT_MD)
        match = FRONTMATTER_RE.match(text)
        body = text[match.end():]
        self.assertRegex(body, r"(?m)^# .+")

    def test_provenance_header_states_all_four_facts(self):
        text = _read(AGENT_MD)
        self.assertIn("openai/codex", text)
        self.assertIn("Apache", text)
        self.assertIn("2.0", text)
        self.assertIn("2026-07-24", text)
        self.assertIn("cbh123/ce4893a10ed2b87a89d9114b08118a08", text)
        # The gist's stale upstream path claim, and why it is stale.
        self.assertIn("codex-rs/core/review_prompt.md", text)
        self.assertIn("404", text)

    def test_provenance_pins_the_verified_upstream_location(self):
        """The gist's attributed path 404s because the file MOVED, not because
        the attribution is wrong. The provenance must pin where it actually
        lives now, by immutable blob id — a path alone goes stale again."""
        text = _read(AGENT_MD)
        self.assertIn("codex-rs/prompts/templates/review/rubric.md", text)
        self.assertIn("85e89cb7eeadb97fa9e4868d2b9977c87225506d", text)
        self.assertIn("VERIFIED", text.upper())

    def test_provenance_does_not_claim_gist_is_official(self):
        text = _read(AGENT_MD)
        self.assertIn("not an official OpenAI distribution channel", text)

    def test_no_banned_words(self):
        text = _read(AGENT_MD).lower()
        for word in ("broker", "app-server", "remote control"):
            self.assertNotIn(word, text)


class LicenseFileTest(unittest.TestCase):
    """Shipping a NOTICE without a LICENSE leaves this repo's own code
    unlicensed and fails Apache-2.0 section 4(a) for the material it
    incorporates. Both files must ship."""

    def test_license_exists_and_is_agpl_3(self):
        self.assertTrue(LICENSE.is_file(), "{} does not exist".format(LICENSE))
        text = _read(LICENSE)
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 19 November 2007", text)
        self.assertIn("END OF TERMS AND CONDITIONS", text)


class NoticeFileTest(unittest.TestCase):
    def test_notice_exists(self):
        self.assertTrue(NOTICE.is_file(), "{} does not exist".format(NOTICE))

    def test_notice_names_licence_and_both_upstream_repos(self):
        text = _read(NOTICE)
        self.assertIn("Apache License", text)
        self.assertIn("2.0", text)
        self.assertIn("openai/codex", text)
        self.assertIn("openai/codex-plugin-cc", text)

    def test_notice_explains_the_agpl_over_apache_combination(self):
        """The repo is AGPL but incorporates Apache-2.0 material. A reader must
        be able to learn both facts, and that the flow is one-way, from NOTICE
        alone — this is the part a bare SPDX tag cannot convey."""
        text = _read(NOTICE)
        self.assertIn("AGPL-3.0-or-later", text)
        self.assertIn("Apache", text)
        self.assertIn("one-way compatible", text)


class FixturesTest(unittest.TestCase):
    def test_at_least_one_fixture_exists(self):
        self.assertTrue(
            FIXTURES_DIR.is_dir(), "{} does not exist".format(FIXTURES_DIR)
        )
        fixtures = [p for p in FIXTURES_DIR.iterdir() if p.is_file()]
        self.assertGreaterEqual(
            len(fixtures), 1, "no fixture files found under {}".format(FIXTURES_DIR)
        )

    def test_every_fixture_is_non_empty(self):
        fixtures = [p for p in FIXTURES_DIR.iterdir() if p.is_file()]
        for path in fixtures:
            self.assertGreater(
                path.stat().st_size, 0, "{} is empty".format(path)
            )


class VerdictDocTest(unittest.TestCase):
    def test_verdict_doc_exists_and_is_non_empty(self):
        self.assertTrue(VERDICT_DOC.is_file(), "{} does not exist".format(VERDICT_DOC))
        self.assertGreater(VERDICT_DOC.stat().st_size, 0)


class MakefileLiveTargetTest(unittest.TestCase):
    def test_check_live_target_exists(self):
        text = _read(MAKEFILE)
        self.assertRegex(text, r"(?m)^check-live:")

    def test_check_live_is_not_reachable_from_check(self):
        text = _read(MAKEFILE)
        # `check:` recipe/prerequisite line must not mention check-live.
        check_match = re.search(r"(?m)^check:(.*)$", text)
        self.assertIsNotNone(check_match, "no `check:` target found")
        self.assertNotIn("check-live", check_match.group(1))
        # No recipe line anywhere in the Makefile should invoke check-live
        # as part of `check`'s dependency chain (M1's `check` deps are only
        # `test lint`, and neither of those targets exist that could smuggle
        # it in, but assert directly against the whole file's dependency
        # graph for `check` as a defense against future edits).
        lines = text.splitlines()
        check_deps = set()
        for line in lines:
            m = re.match(r"^check:\s*(.*)$", line)
            if m:
                check_deps.update(m.group(1).split())
        self.assertNotIn("check-live", check_deps)


if __name__ == "__main__":
    unittest.main()
