"""companion.agent_workspace — put a vendored agent where `agy` will actually
find it.

`agy` resolves a workspace-scoped custom agent from the **run's working
directory**, at the literal path `{workspace}/.agents/agents/{name}/agent.md`,
and only when `--new-project` is passed (docs/review-schema-verdict.md,
Finding B). The vendored agents ship inside this plugin at
`plugins/agy/agents/<name>/agent.md` — a path `agy` never looks at. Without
this module every `/agy:review` silently falls back to agy's default agent,
which is exactly what `/agy:setup`'s bind probe reports as NOT BOUND.

The fix is an **agent workspace**: a directory containing nothing but
`.agents/agents/<name>/agent.md`, used as the run's cwd. The reviewed repo is
attached with `--add-dir` instead, so nothing is ever written into it. This
works because the diff is already embedded in the prompt (see
`companion.review`'s `_REVIEW_INSTRUCTIONS_TEMPLATE` and Finding C) — the
agent does not need to read the repo at all today.

Two lifetimes, because the two launch paths differ:

- `ephemeral()` — a temp dir for the duration of a blocking foreground run
  (and for `/agy:setup`'s probe). Deleted on exit.
- `materialize()` — into a caller-owned directory that must outlive this
  process, for a **detached background job**. A TemporaryDirectory here would
  be deleted the moment the parent returns, pulling the agent out from under
  a job that has only just started.

Verified against agy 1.1.8: a non-git temp dir as cwd plus `--add-dir <repo>`
binds cleanly (no `printmode.go` fallback line).

If these agents ever gain real file-exploration tools, the `--add-dir`
attachment stops being a nicety and becomes the only thing making the repo
readable at all — do not drop it as "unused".
"""
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

# The binary's literal template, quoted from Finding B's ledger:
# `{workspace}/.agents/agents/{agent_name}/`. Every other placement tried
# (.agent/, _agents/, .gemini/, flat <name>.md) fell back. Do not "tidy" this.
_AGENTS_SUBPATH = Path(".agents") / "agents"

_AGENT_FILE_NAME = "agent.md"


class AgentWorkspaceError(Exception):
    """The vendored agent file is missing or could not be staged."""


def vendored_agent_path(agent_name):
    """Absolute path to the agent shipped inside this plugin. This file is
    `plugins/agy/scripts/companion/agent_workspace.py`, so the agents dir is
    two parents up — resolved from `__file__` rather than
    `$CLAUDE_PLUGIN_ROOT` so it works the same whether the companion is run
    by a slash command, by a test, or by hand."""
    return Path(__file__).resolve().parents[2] / "agents" / agent_name / _AGENT_FILE_NAME


def materialize(root, agent_name):
    """Stage `agent_name` into `root/.agents/agents/<name>/agent.md` and
    return `root` as a Path, ready to be used as an `agy` run's cwd. Creates
    parent dirs; overwrites any stale copy. Raises AgentWorkspaceError if the
    vendored agent is missing, so a broken install surfaces as that rather
    than as a silent fallback 300 seconds later."""
    source = vendored_agent_path(agent_name)
    if not source.is_file():
        raise AgentWorkspaceError(
            "vendored agent \"{}\" not found at {} — the plugin install is "
            "incomplete; /agy:review cannot bind without it.".format(agent_name, source)
        )

    root_path = Path(root)
    agent_dir = root_path / _AGENTS_SUBPATH / agent_name
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(agent_dir / _AGENT_FILE_NAME))
    except OSError as exc:
        raise AgentWorkspaceError(
            "could not stage agent \"{}\" into {}: {}".format(agent_name, root_path, exc)
        )
    return root_path


@contextmanager
def ephemeral(agent_name, prefix="agy-agent-ws-"):
    """An agent workspace that lives exactly as long as the `with` block —
    for blocking foreground runs and probes only. Never use this for a
    detached background job: the directory is removed as soon as the block
    exits, while the job is still running."""
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        yield materialize(tmp, agent_name)
