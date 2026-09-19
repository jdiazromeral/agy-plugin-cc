#!/usr/bin/env python3
"""tools/lint.py — the companion's lint pass, wired into `make check`.

Four checks, stdlib only, no network, no pip install:
  1. syntax  — every shipped .py file compiles under this repo's python3.
  2. banned words — "broker", "app-server", "remote control" must not
     appear in shipped code or command surfaces (plugins/agy/**). These
     words are deliberately used in AGENTS.md and .looper/ docs to name
     what NOT to build; the scan is scoped to shipped surfaces only.
  3. manifest JSON — marketplace.json and plugin.json must parse as JSON.
  4. model invocation — a command file whose body invokes the companion
     with a subcommand its own module marks DANGEROUS (write-capable or
     destructive — the flag lives next to that module's HELP constant,
     never in a second list here) must declare
     `disable-model-invocation: true` in its own frontmatter, so Claude
     Code cannot fire it on its own **model invocation** judgement. See
     .looper/knowledge/glossary.md for the term.
"""
import compileall
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "agy"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"

BANNED_WORDS = ("broker", "app-server", "remote control")
_SCANNED_SUFFIXES = (".py", ".md", ".json")

MANIFESTS = (
    REPO_ROOT / ".claude-plugin" / "marketplace.json",
    PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
# Matches the companion's own literal invocation shape, e.g.
# `agy_companion.py" review $ARGUMENTS` or `agy_companion.py delegate ...` —
# the subcommand name is whatever word immediately follows the script path.
_INVOCATION_RE = re.compile(r"agy_companion\.py[\"']?\s+([a-z][a-z-]*)")


def _check_syntax():
    ok = True
    for directory in (PLUGIN_ROOT, REPO_ROOT / "tools", REPO_ROOT / "tests"):
        if directory.exists():
            ok = compileall.compile_dir(str(directory), quiet=1, force=True) and ok
    return ok


def _check_banned_words():
    problems = []
    for path in PLUGIN_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for word in BANNED_WORDS:
            if word in text:
                problems.append(
                    "{}: banned word '{}'".format(path.relative_to(REPO_ROOT), word)
                )
    return problems


def _check_manifests():
    problems = []
    for manifest in MANIFESTS:
        if not manifest.exists():
            problems.append("{}: missing".format(manifest.relative_to(REPO_ROOT)))
            continue
        try:
            json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(
                "{}: invalid JSON ({})".format(manifest.relative_to(REPO_ROOT), exc)
            )
    return problems


def _frontmatter_text(path):
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def _frontmatter_field(path, field):
    for line in _frontmatter_text(path).splitlines():
        if line.startswith(field + ":"):
            return line.split(":", 1)[1].strip()
    return None


def _declares_disable_model_invocation(path):
    return _frontmatter_field(path, "disable-model-invocation") == "true"


def _direct_subcommands(text, known_names):
    return {name for name in _INVOCATION_RE.findall(text) if name in known_names}


def _load_agents(agents_dir):
    """Map an agent's declared name (frontmatter `name:`, falling back to
    its filename/directory stem) to its .md file. Agent files live either
    flat (`agents/<name>.md`) or in a subdirectory (`agents/<name>/agent.md`)
    — both shapes are shipped in this repo today."""
    agents = {}
    if not agents_dir.exists():
        return agents
    for path in agents_dir.glob("*.md"):
        name = _frontmatter_field(path, "name") or path.stem
        agents[name] = path
    for path in agents_dir.glob("*/agent.md"):
        name = _frontmatter_field(path, "name") or path.parent.name
        agents[name] = path
    return agents


def _resolve_subcommands(command_path, agents, known_names):
    """Which companion subcommand(s) does this command file's body reach?
    First look for a direct `agy_companion.py <subcommand>` invocation in
    the command's own text. If none is found, the command's body instead
    names an Agent-tool forward to a subagent (e.g. /agy:delegate forwards
    to agy-delegate) — find which known agent name is mentioned in the
    text and resolve the subcommand from THAT file's own direct
    invocation instead. Never a hardcoded command->agent pair."""
    text = command_path.read_text(encoding="utf-8")
    direct = _direct_subcommands(text, known_names)
    if direct:
        return direct
    for agent_name, agent_path in agents.items():
        if re.search(r"\b" + re.escape(agent_name) + r"\b", text):
            agent_text = agent_path.read_text(encoding="utf-8")
            forwarded = _direct_subcommands(agent_text, known_names)
            if forwarded:
                return forwarded
    return set()


def _model_invocation_problems(commands_dir, agents_dir, subcommands):
    problems = []
    if not commands_dir.exists():
        return problems
    agents = _load_agents(agents_dir)
    known_names = set(subcommands)
    for command_path in sorted(commands_dir.glob("*.md")):
        reached = _resolve_subcommands(command_path, agents, known_names)
        dangerous = sorted(
            name for name in reached if getattr(subcommands[name], "DANGEROUS", False)
        )
        if dangerous and not _declares_disable_model_invocation(command_path):
            try:
                label = command_path.relative_to(REPO_ROOT)
            except ValueError:
                label = command_path
            problems.append(
                "{}: invokes dangerous subcommand(s) {} without "
                "disable-model-invocation: true".format(
                    label, ", ".join(dangerous)
                )
            )
    return problems


def _check_model_invocation():
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
    from companion import cli  # local import: only needed for this one check

    return _model_invocation_problems(COMMANDS_DIR, AGENTS_DIR, cli._SUBCOMMANDS)


def main():
    problems = []
    if not _check_syntax():
        problems.append("syntax check failed (see compileall output above)")
    problems.extend(_check_banned_words())
    problems.extend(_check_manifests())
    problems.extend(_check_model_invocation())

    if problems:
        for problem in problems:
            print("lint: {}".format(problem), file=sys.stderr)
        return 1

    print("lint: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
