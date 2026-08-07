---
description: Show active and recent agy jobs for this repo (status, conversation, elapsed, log tail)
argument-hint: '[--repo <path>] [--json] [--all-sessions]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" status $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports.

`--repo <path>` names the repository whose jobs to read when the session is
rooted somewhere else — a multi-repo workspace root, say. Omit it and the
current working directory is used, exactly as before. Jobs are scoped to a
repo's own state dir, so this must match the repo the job was launched
against or it will not be found.

Reads the per-repo state dir and, for each active or recent job, parses its
persistent log to derive status (running, completed, or a distinct error
state for a silent fallback or a bound-but-crashed run), the conversation
UUID once known, elapsed/duration, and a short log tail — rendered as a
compact table. Scoped to the current repo, and to the current Claude
session when a session id is available (pass `--all-sessions` to see every
job for the repo instead).

This command only reads job state; it does not fetch a job's full result
(use `/agy:result`) or stop a running job (use `/agy:cancel`).
