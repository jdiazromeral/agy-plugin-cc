#!/usr/bin/env python3
"""tools/lint.py — the companion's lint pass, wired into `make check`.

Three checks, stdlib only, no network, no pip install:
  1. syntax  — every shipped .py file compiles under this repo's python3.
  2. banned words — "broker", "app-server", "remote control" must not
     appear in shipped code or command surfaces (plugins/agy/**). These
     words are deliberately used in AGENTS.md and .looper/ docs to name
     what NOT to build; the scan is scoped to shipped surfaces only.
  3. manifest JSON — marketplace.json and plugin.json must parse as JSON.
"""
import compileall
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "agy"

BANNED_WORDS = ("broker", "app-server", "remote control")
_SCANNED_SUFFIXES = (".py", ".md", ".json")

MANIFESTS = (
    REPO_ROOT / ".claude-plugin" / "marketplace.json",
    PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
)


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


def main():
    problems = []
    if not _check_syntax():
        problems.append("syntax check failed (see compileall output above)")
    problems.extend(_check_banned_words())
    problems.extend(_check_manifests())

    if problems:
        for problem in problems:
            print("lint: {}".format(problem), file=sys.stderr)
        return 1

    print("lint: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
