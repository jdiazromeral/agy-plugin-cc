"""Behavior tests for `companion.probe` — the free, read-only slash-command
probe: `agy -p "/<cmd>" --output-format json`.

The pure argv builder (`_probe_command`) is asserted directly, mirroring
`tests/test_setup.py`'s `BindProbeCommandVectorTest` and
`tests/test_delegate.py`'s `_agy_command` coverage. `_run_probe`'s tri-state
behavior is exercised with `subprocess.run` mocked via
`mock.patch.object(probe.subprocess, "run", ...)`, the same technique
`tests/test_timeout_flag.py` uses — no real `agy` and no fake-agy subprocess
needed to prove a pure JSON-parsing contract. The full round-trip through a
real subprocess and `tests/fake_agy.py`'s carve-out is instead covered by
`tests/test_setup.py`, which exercises `_run_probe` through the doctor.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import probe  # noqa: E402


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class ProbeCommandVectorTest(unittest.TestCase):
    """`_probe_command` is pure — no I/O — so it is asserted directly."""

    def test_bare_probe_vector(self):
        cmd = probe._probe_command("agy", "model")
        self.assertEqual(cmd, ["agy", "-p", "/model", "--output-format", "json"])

    def test_disable_slash_commands_is_absent(self):
        """Regression guard: every OTHER command vector in this plugin
        carries --disable-slash-commands unconditionally
        (delegate._agy_command, review._agy_command,
        setup._bind_probe_command). This is the one deliberate exception —
        see companion/probe.py's module docstring for why omitting it here
        is correct, not an oversight. If this test ever fails, someone
        "fixed" the omission back in and broken the probe: with the flag
        present, agy sends "/model" to the model as literal prompt text
        instead of answering it for free (verified fact, see module
        docstring)."""
        cmd = probe._probe_command("agy", "model")
        self.assertNotIn("--disable-slash-commands", cmd)

    def test_forwards_model_and_effort_when_given(self):
        cmd = probe._probe_command("agy", "model", model="gemini-3.1-pro-low", effort="low")
        self.assertEqual(cmd, [
            "agy", "-p", "/model", "--output-format", "json",
            "--model", "gemini-3.1-pro-low",
            "--effort", "low",
        ])

    def test_model_without_effort_omits_effort_flag(self):
        cmd = probe._probe_command("agy", "model", model="gemini-3.1-pro-low")
        self.assertIn("--model", cmd)
        self.assertNotIn("--effort", cmd)

    def test_rejects_a_command_outside_the_read_only_whitelist(self):
        with self.assertRaises(ValueError):
            probe._probe_command("agy", "clear")

    def test_every_documented_read_only_command_is_accepted(self):
        for command in (
            "usage",
            "credits",
            "model",
            "effort",
            "skills",
            "permissions",
            "hooks",
            "help",
            "changelog",
            "config",
        ):
            cmd = probe._probe_command("agy", command)
            self.assertEqual(cmd[2], "/" + command)


class RunProbeSuccessTest(unittest.TestCase):
    def test_run_probe_passes_devnull_stdin(self):
        envelope = '{"command":{"name":"model","data":{"id":"m"}}}'
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=envelope)) as mock_run:
            probe._run_probe("agy", "model")

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_ok_state_returns_the_typed_command_data(self):
        envelope = (
            '{"conversation_id":"","status":"SUCCESS","response":"",'
            '"duration_seconds":0,"num_turns":0,'
            '"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,'
            '"cache_read_tokens":0,"total_tokens":0},'
            '"command":{"name":"model","data":{"id":"gemini-3.1-pro-high",'
            '"label":"Gemini 3.1 Pro (High)","effort":"high","is_default":true}}}'
        )
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=envelope)):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["command"], "model")
        self.assertEqual(result["data"], {
            "id": "gemini-3.1-pro-high",
            "label": "Gemini 3.1 Pro (High)",
            "effort": "high",
            "is_default": True,
        })
        self.assertIsNone(result["detail"])

    def test_empty_data_object_is_still_ok(self):
        """`/skills` on an account with none registered is a legitimate
        empty answer, not a failure — an empty dict must not be treated the
        same as a missing "data" key."""
        envelope = '{"command":{"name":"skills","data":{}}}'
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=envelope)):
            result = probe._run_probe("agy", "skills")

        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["data"], {})


class RunProbeUnknownPathsTest(unittest.TestCase):
    """Fail closed, never guess: every one of these must report "unknown",
    never "ok" and never crash."""

    def test_non_zero_exit_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run",
            return_value=_result(returncode=1, stderr="Error: invalid model selection"),
        ):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["data"])
        self.assertIn("invalid model selection", result["detail"])

    def test_unparseable_json_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run", return_value=_result(stdout="not json at all"),
        ):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["data"])

    def test_json_that_is_not_an_object_is_unknown(self):
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout="[1, 2, 3]")):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")

    def test_missing_command_key_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run", return_value=_result(stdout='{"status":"SUCCESS"}'),
        ):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIn("command", result["detail"])

    def test_command_name_mismatch_is_unknown(self):
        """`_run_probe` must never trust a payload it did not verify
        answers the command it actually asked for."""
        envelope = '{"command":{"name":"usage","data":{}}}'
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=envelope)):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIn("model", result["detail"])
        self.assertIn("usage", result["detail"])

    def test_command_object_missing_data_key_is_unknown(self):
        envelope = '{"command":{"name":"model"}}'
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=envelope)):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")

    def test_timeout_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd=["agy"], timeout=20),
        ):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["data"])

    def test_agy_not_on_path_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run", side_effect=FileNotFoundError("no such file: agy"),
        ):
            result = probe._run_probe("agy", "model")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["data"])


class ModelsCommandVectorTest(unittest.TestCase):
    """`_models_command` is pure — no I/O. `agy models` is a real, separate
    subcommand (no `-p`, no slash command), used by
    `setup._probe_model_forwarding` to pick a probe target guaranteed to
    differ from whatever `/model` reports as the baseline — see that
    function's docstring for why forwarding the SAME value the baseline
    reads proves nothing."""

    def test_vector_is_just_models(self):
        self.assertEqual(probe._models_command("agy"), ["agy", "models"])


class ParseModelsTest(unittest.TestCase):
    """`_parse_models` is a tolerant parse of agy's own freeform CLI
    output, not a JSON contract — malformed lines are skipped, never
    raised on."""

    def test_parses_the_real_1_1_11_shape(self):
        stdout = (
            "Fetching available models...\n"
            "gemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
            "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
        )
        self.assertEqual(probe._parse_models(stdout), [
            ("gemini-3.1-pro-high", "Gemini 3.1 Pro (High)"),
            ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
        ])

    def test_header_line_is_skipped(self):
        stdout = "Fetching available models...\ngemini-x\tGemini X\n"
        ids = [model_id for model_id, _label in probe._parse_models(stdout)]
        self.assertNotIn("Fetching available models...", ids)
        self.assertEqual(ids, ["gemini-x"])

    def test_blank_lines_are_skipped(self):
        stdout = "Fetching available models...\n\n\ngemini-x\tGemini X\n\n"
        self.assertEqual(probe._parse_models(stdout), [("gemini-x", "Gemini X")])

    def test_lines_without_a_tab_are_skipped(self):
        stdout = "Fetching available models...\nsome banner text with no tab\ngemini-x\tGemini X\n"
        self.assertEqual(probe._parse_models(stdout), [("gemini-x", "Gemini X")])

    def test_empty_stdout_yields_an_empty_list(self):
        self.assertEqual(probe._parse_models(""), [])


class RunModelsTest(unittest.TestCase):
    def test_run_models_passes_devnull_stdin(self):
        stdout = "gemini-x\tGemini X\n"
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=stdout)) as mock_run:
            probe._run_models("agy")

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_ok_state_returns_parsed_models(self):
        stdout = "Fetching available models...\ngemini-x\tGemini X\ngemini-y\tGemini Y\n"
        with mock.patch.object(probe.subprocess, "run", return_value=_result(stdout=stdout)):
            result = probe._run_models("agy")

        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["models"], [("gemini-x", "Gemini X"), ("gemini-y", "Gemini Y")])
        self.assertIsNone(result["detail"])

    def test_non_zero_exit_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run",
            return_value=_result(returncode=1, stderr="Error: could not fetch models"),
        ):
            result = probe._run_models("agy")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["models"])
        self.assertIn("could not fetch models", result["detail"])

    def test_no_parseable_lines_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run",
            return_value=_result(stdout="Fetching available models...\nno tabs anywhere\n"),
        ):
            result = probe._run_models("agy")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["models"])

    def test_timeout_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd=["agy", "models"], timeout=20),
        ):
            result = probe._run_models("agy")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["models"])

    def test_agy_not_on_path_is_unknown(self):
        with mock.patch.object(
            probe.subprocess, "run", side_effect=FileNotFoundError("no such file: agy"),
        ):
            result = probe._run_models("agy")

        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["models"])


if __name__ == "__main__":
    unittest.main()
