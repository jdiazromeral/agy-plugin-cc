import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.launch import format_agy_error, parse_agy_error


class AgyErrorParsingTest(unittest.TestCase):
    def test_parse_empty_or_none(self):
        self.assertIsNone(parse_agy_error(""))
        self.assertIsNone(parse_agy_error(None))

    def test_parse_plain_stderr_returns_none(self):
        stderr = "some random error message\nwarning: something happened"
        self.assertIsNone(parse_agy_error(stderr))

    def test_parse_structured_agy_error_line(self):
        stderr = (
            "I0918 20:00:00 server.go:123] Starting up\n"
            'AGY_ERROR: {"code": 403, "status": "PERMISSION_DENIED", "message": "Model quota exhausted"}\n'
            "I0918 20:00:01 server.go:456] Exiting\n"
        )
        parsed = parse_agy_error(stderr)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("code"), 403)
        self.assertEqual(parsed.get("status"), "PERMISSION_DENIED")
        self.assertEqual(parsed.get("message"), "Model quota exhausted")

    def test_format_agy_error_with_code_and_message(self):
        stderr = 'AGY_ERROR: {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Rate limit exceeded"}'
        formatted = format_agy_error(stderr)
        self.assertEqual(formatted, "agy error [429]: Rate limit exceeded")

    def test_format_agy_error_with_message_only(self):
        stderr = 'AGY_ERROR: {"message": "Invalid configuration"}'
        formatted = format_agy_error(stderr)
        self.assertEqual(formatted, "agy error: Invalid configuration")

    def test_format_agy_error_fallback_to_plain_stderr(self):
        stderr = "  fatal: unexpected end of file  \n"
        self.assertEqual(format_agy_error(stderr), "fatal: unexpected end of file")


if __name__ == "__main__":
    unittest.main()
