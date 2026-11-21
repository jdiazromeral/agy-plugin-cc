import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_PATH = (
    REPO_ROOT
    / "plugins"
    / "agy"
    / "skills"
    / "agy-result-handling"
    / "SKILL.md"
)


class ResultHandlingSkillTest(unittest.TestCase):
    def test_skill_file_exists(self):
        self.assertTrue(SKILL_PATH.is_file(), "SKILL.md must exist at {}".format(SKILL_PATH))

    def test_frontmatter_name_and_invocability(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\n"))
        _, frontmatter, body = content.split("---\n", 2)
        self.assertIn("name: agy-result-handling", frontmatter)
        self.assertIn("user-invocable: false", frontmatter)

    def test_critical_guard_against_auto_apply_fixes(self):
        content = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Auto-applying fixes from a review is strictly forbidden",
            content,
            "Skill must forbid Claude from auto-fixing findings without user consent",
        )

    def test_no_banned_words(self):
        content = SKILL_PATH.read_text(encoding="utf-8").lower()
        for banned in ("broker", "app-server", "remote control"):
            self.assertNotIn(banned, content)


if __name__ == "__main__":
    unittest.main()
