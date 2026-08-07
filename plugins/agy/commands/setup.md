---
description: Check whether agy is installed, authenticated, and which agents are registered
argument-hint: '[--json]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" setup $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports. It already distinguishes agy not installed,
present-but-not-authenticated, and present-and-authenticated, and lists
registered agents (an empty list is a legitimate result, not an error). It
also probes whether the vendored `agy-review` agent actually binds — `agy
agents` only ever lists globally registered agents, never this
workspace-scoped one — and reports plainly whether `/agy:review` will work.
