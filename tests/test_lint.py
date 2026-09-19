"""Tests for tools/lint.py's fourth check — the **model invocation** guard.

Closes the model-invocation exposure Claude Code 2.1.278 introduced: a
command file whose body invokes the companion with a subcommand its own
module marks dangerous (write-capable or destructive — see each companion
subcommand module's `DANGEROUS` flag next to its `HELP` constant) must
declare `disable-model-invocation: true` in its own frontmatter, or lint
fails. The five read-only commands are never required to declare it.

`lint._check_model_invocation()` derives this from content: it walks
`plugins/agy/commands/*.md`, resolves which companion subcommand each one
invokes (directly, or — for a command that forwards to a subagent via the
`Agent` tool, like `/agy:delegate` — by reading the referenced agent file
under `plugins/agy/agents/`), and looks up that subcommand's own module for
the `DANGEROUS` marker. Never a hardcoded list of command filenames — the
fixture test below proves the rule is genuinely content-derived, not two
special-cased paths.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import lint  # noqa: E402

COMMANDS_DIR = REPO_ROOT / "plugins" / "agy" / "commands"
AGENTS_DIR = REPO_ROOT / "plugins" / "agy" / "agents"

_READ_ONLY_COMMANDS = (
    "review.md",
    "adversarial-review.md",
    "setup.md",
    "status.md",
    "result.md",
)


class ModelInvocationCheckTest(unittest.TestCase):
    def test_dangerous_commands_declare_disable_model_invocation(self):
        # delegate.md and cancel.md invoke dangerous subcommands (delegate
        # is write-capable, cancel is destructive — see AGENTS.md) and must
        # both carry the frontmatter flag.
        for name in ("delegate.md", "cancel.md"):
            text = (COMMANDS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(
                "disable-model-invocation: true",
                text,
                "{} must declare disable-model-invocation: true".format(name),
            )

        problems = lint._check_model_invocation()
        flagged = [p for p in problems if "delegate.md" in p or "cancel.md" in p]
        self.assertEqual(
            flagged, [], "dangerous commands still flagged: {}".format(flagged)
        )

    def test_read_only_commands_not_required_to_declare_flag(self):
        problems = lint._check_model_invocation()
        for name in _READ_ONLY_COMMANDS:
            text = (COMMANDS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "disable-model-invocation",
                text,
                "{} must stay model-invocable (no flag)".format(name),
            )
            flagged = [p for p in problems if name in p]
            self.assertEqual(
                flagged, [], "read-only command wrongly flagged: {}".format(flagged)
            )

    def test_check_flags_a_fresh_dangerous_command_missing_the_flag(self):
        # Regression test: prove the rule is derived from a subcommand
        # module's own DANGEROUS marker and a command's own invocation
        # text — not hardcoded to "delegate.md"/"cancel.md" — by fabricating
        # a third dangerous subcommand and a command that invokes it
        # without the frontmatter flag.
        fake_subcommands = {
            "obliterate": SimpleNamespace(DANGEROUS=True),
            "peek": SimpleNamespace(DANGEROUS=False),
        }
        with tempfile.TemporaryDirectory() as tmp:
            commands_dir = Path(tmp) / "commands"
            agents_dir = Path(tmp) / "agents"
            commands_dir.mkdir()
            agents_dir.mkdir()

            unflagged = commands_dir / "wipe.md"
            unflagged.write_text(
                "---\n"
                "description: wipe everything\n"
                "---\n\n"
                "Run:\n\n"
                "```bash\n"
                'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" obliterate $ARGUMENTS\n'
                "```\n",
                encoding="utf-8",
            )
            flagged = commands_dir / "wipe-safe.md"
            flagged.write_text(
                "---\n"
                "description: wipe everything, guarded\n"
                "disable-model-invocation: true\n"
                "---\n\n"
                "Run:\n\n"
                "```bash\n"
                'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" obliterate $ARGUMENTS\n'
                "```\n",
                encoding="utf-8",
            )
            harmless = commands_dir / "peek.md"
            harmless.write_text(
                "---\n"
                "description: read something\n"
                "---\n\n"
                "Run:\n\n"
                "```bash\n"
                'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" peek $ARGUMENTS\n'
                "```\n",
                encoding="utf-8",
            )

            problems = lint._model_invocation_problems(
                commands_dir, agents_dir, fake_subcommands
            )

        problem_files = {Path(p.split(":", 1)[0]).name for p in problems}
        self.assertIn("wipe.md", problem_files)
        self.assertNotIn("wipe-safe.md", problem_files)
        self.assertNotIn("peek.md", problem_files)

    def test_check_resolves_subcommand_through_a_subagent_forward(self):
        # /agy:delegate never invokes the companion directly in its own
        # body — it forwards to the agy-delegate subagent, whose file is
        # the one with the literal invocation. Prove the resolver follows
        # that forward with a fabricated command + agent pair, rather than
        # relying on delegate.md/agy-delegate.md alone (which the first
        # test already covers end to end).
        fake_subcommands = {"obliterate": SimpleNamespace(DANGEROUS=True)}
        with tempfile.TemporaryDirectory() as tmp:
            commands_dir = Path(tmp) / "commands"
            agents_dir = Path(tmp) / "agents"
            commands_dir.mkdir()
            agents_dir.mkdir()

            (commands_dir / "delegate-wipe.md").write_text(
                "---\n"
                "description: forwards to a subagent\n"
                "---\n\n"
                "Invoke the `wipe-agent` subagent via the `Agent` tool.\n",
                encoding="utf-8",
            )
            (agents_dir / "wipe-agent.md").write_text(
                "---\n"
                "name: wipe-agent\n"
                "description: thin forwarder\n"
                "---\n\n"
                "Use exactly one `Bash` call to invoke "
                '`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" obliterate ...`.\n',
                encoding="utf-8",
            )

            problems = lint._model_invocation_problems(
                commands_dir, agents_dir, fake_subcommands
            )

        problem_files = {Path(p.split(":", 1)[0]).name for p in problems}
        self.assertIn("delegate-wipe.md", problem_files)


class ManifestSchemaTest(unittest.TestCase):
    def test_marketplace_manifest_declares_schema(self):
        import json

        manifest = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest.get("$schema"),
            "https://json.schemastore.org/claude-code-marketplace.json",
        )

    def test_plugin_manifest_declares_schema(self):
        import json

        manifest = json.loads(
            (
                REPO_ROOT / "plugins" / "agy" / ".claude-plugin" / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest.get("$schema"),
            "https://json.schemastore.org/claude-code-plugin-manifest.json",
        )


if __name__ == "__main__":
    unittest.main()
