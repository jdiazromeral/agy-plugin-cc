"""Tests for `companion.agent_workspace` — the **agent workspace** that makes
a vendored agent findable by `agy`.

The regression these guard: the vendored agents ship at
`plugins/agy/agents/<name>/agent.md`, but `agy` resolves a workspace-scoped
agent only from `{cwd}/.agents/agents/<name>/agent.md` (Finding B). Nothing
bridged the two, so every `/agy:review` silently fell back to agy's default
agent. Assertions here are on the **exact placement** — a "close enough" path
binds nothing.

No `agy` is invoked anywhere in this file; it is pure filesystem behavior.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import agent_workspace  # noqa: E402
from companion.agent_workspace import AgentWorkspaceError  # noqa: E402

_REVIEW = "agy-review"
_ADVERSARIAL = "agy-adversarial-review"


class VendoredAgentPathTest(unittest.TestCase):
    def test_both_shipped_agents_resolve_to_real_files(self):
        """If this fails, the plugin cannot bind anything — either an agent
        was moved/renamed, or this module's parents[2] hop is wrong."""
        for name in (_REVIEW, _ADVERSARIAL):
            path = agent_workspace.vendored_agent_path(name)
            self.assertTrue(path.is_file(), "vendored agent missing: {}".format(path))

    def test_resolved_path_is_the_documented_shipping_location(self):
        self.assertEqual(
            agent_workspace.vendored_agent_path(_REVIEW),
            REPO_ROOT / "plugins" / "agy" / "agents" / _REVIEW / "agent.md",
        )


class MaterializeTest(unittest.TestCase):
    def test_stages_the_agent_at_the_exact_path_agy_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = agent_workspace.materialize(tmp, _REVIEW)
            staged = Path(root) / ".agents" / "agents" / _REVIEW / "agent.md"
            self.assertTrue(staged.is_file())
            self.assertEqual(
                staged.read_text(encoding="utf-8"),
                agent_workspace.vendored_agent_path(_REVIEW).read_text(encoding="utf-8"),
            )

    def test_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "does" / "not" / "exist" / "yet"
            agent_workspace.materialize(nested, _REVIEW)
            self.assertTrue((nested / ".agents" / "agents" / _REVIEW / "agent.md").is_file())

    def test_overwrites_a_stale_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / ".agents" / "agents" / _REVIEW / "agent.md"
            staged.parent.mkdir(parents=True)
            staged.write_text("stale content from an older plugin version", encoding="utf-8")
            agent_workspace.materialize(tmp, _REVIEW)
            self.assertNotIn("stale content", staged.read_text(encoding="utf-8"))

    def test_missing_vendored_agent_raises_rather_than_staging_nothing(self):
        """A broken install must fail loudly here, not 300 seconds later as a
        silent fallback that reads like a real (but wrong) review."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AgentWorkspaceError) as caught:
                agent_workspace.materialize(tmp, "agent-that-does-not-exist")
            self.assertIn("agent-that-does-not-exist", str(caught.exception))

    def test_adversarial_agent_stages_under_its_own_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_workspace.materialize(tmp, _ADVERSARIAL)
            self.assertTrue(
                (Path(tmp) / ".agents" / "agents" / _ADVERSARIAL / "agent.md").is_file()
            )
            # The two agents must not collide: staging one never creates the other.
            self.assertFalse((Path(tmp) / ".agents" / "agents" / _REVIEW).exists())


class EphemeralTest(unittest.TestCase):
    def test_workspace_exists_inside_the_block_and_is_gone_after(self):
        with agent_workspace.ephemeral(_REVIEW) as workspace:
            staged = Path(workspace) / ".agents" / "agents" / _REVIEW / "agent.md"
            self.assertTrue(staged.is_file())
            captured = Path(workspace)
        self.assertFalse(captured.exists(), "ephemeral workspace outlived its block")

    def test_missing_agent_raises_on_enter(self):
        with self.assertRaises(AgentWorkspaceError):
            with agent_workspace.ephemeral("agent-that-does-not-exist"):
                self.fail("should not have entered the block")


if __name__ == "__main__":
    unittest.main()
