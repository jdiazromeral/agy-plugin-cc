"""Tests for companion.review_output — the tolerant parse and table
rendering of agy-review's stdout. Proven against the two real captured
fixtures in tests/fixtures/review/ (M2 ledger rows 5 and 6), plus synthetic
inputs for the defenses the fixtures don't happen to exercise (markdown
fences, missing keys, non-JSON text) per docs/review-schema-verdict.md's
Recommendation to M4.

Pure functions only — no subprocess, no agy, no network.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.review_output import format_priority, render_review, tolerant_parse  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "review"


def _load_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TolerantParseFixtureTest(unittest.TestCase):
    """The two committed real fixtures are the golden inputs the contract
    requires the tolerant parse to be proven against."""

    def test_run3_absolute_path_fixture_parses_with_p0_finding_and_verdict(self):
        parsed = tolerant_parse(_load_fixture("2026-07-24-run3.stdout.txt"))

        self.assertTrue(parsed["ok"], parsed.get("reason"))
        self.assertEqual(parsed["overall_correctness"], "patch is incorrect")
        self.assertEqual(len(parsed["findings"]), 1)
        self.assertEqual(parsed["findings"][0]["priority"], 0)
        self.assertTrue(
            parsed["findings"][0]["code_location"]["absolute_file_path"].startswith("/")
        )

    def test_run4_relative_absolute_file_path_fixture_still_parses(self):
        """run4's code_location.absolute_file_path is 'calc.py' — relative
        despite the field's name (the one concrete schema deviation the
        mission observed). The tolerant parse must not choke on it."""
        parsed = tolerant_parse(_load_fixture("2026-07-24-run4.stdout.txt"))

        self.assertTrue(parsed["ok"], parsed.get("reason"))
        self.assertEqual(parsed["overall_correctness"], "patch is incorrect")
        self.assertEqual(
            parsed["findings"][0]["code_location"]["absolute_file_path"], "calc.py"
        )


class RenderReviewFixtureTest(unittest.TestCase):
    def test_render_run3_shows_p0_priority_and_verdict_in_a_table(self):
        parsed = tolerant_parse(_load_fixture("2026-07-24-run3.stdout.txt"))

        rendered = render_review(parsed)

        self.assertIn("P0", rendered)
        self.assertIn("|", rendered)  # a table, not prose
        self.assertIn("patch is incorrect", rendered)

    def test_render_run4_shows_relative_path_verbatim_as_display_text(self):
        parsed = tolerant_parse(_load_fixture("2026-07-24-run4.stdout.txt"))

        rendered = render_review(parsed)

        self.assertIn("calc.py", rendered)


class TolerantParseDefenseTest(unittest.TestCase):
    """Recommendation to M4, items 4-5: markdown fences / prose wrapping,
    and missing/mistyped keys — not observed in the mission's 2 captures but
    defended against on general principle."""

    def test_markdown_fenced_json_with_prose_is_extracted(self):
        wrapped = (
            "Sure, here is the review:\n\n```json\n"
            + _load_fixture("2026-07-24-run3.stdout.txt")
            + "\n```\n\nLet me know if you have questions."
        )

        parsed = tolerant_parse(wrapped)

        self.assertTrue(parsed["ok"], parsed.get("reason"))
        self.assertEqual(parsed["overall_correctness"], "patch is incorrect")

    def test_missing_required_top_level_key_falls_back_to_raw_text(self):
        broken = '{"findings": [], "overall_correctness": "patch is correct"}'

        parsed = tolerant_parse(broken)

        self.assertFalse(parsed["ok"])
        self.assertEqual(render_review(parsed), broken)

    def test_non_json_text_falls_back_to_raw_text_not_an_error(self):
        text = "I could not complete the review due to an internal error."

        parsed = tolerant_parse(text)

        self.assertFalse(parsed["ok"])
        self.assertEqual(render_review(parsed), text)

    def test_malformed_json_falls_back_to_raw_text(self):
        text = '{"findings": [}'

        parsed = tolerant_parse(text)

        self.assertFalse(parsed["ok"])
        self.assertEqual(render_review(parsed), text)


class FormatPriorityTest(unittest.TestCase):
    def test_maps_int_priority_0_to_3_onto_p_labels(self):
        self.assertEqual(format_priority(0), "P0")
        self.assertEqual(format_priority(1), "P1")
        self.assertEqual(format_priority(2), "P2")
        self.assertEqual(format_priority(3), "P3")

    def test_missing_priority_renders_as_p_unknown(self):
        self.assertEqual(format_priority(None), "P?")


if __name__ == "__main__":
    unittest.main()
