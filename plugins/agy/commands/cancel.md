---
description: Cancel a running background agy job for this repo
argument-hint: '[--repo <path>] [job-id]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" cancel $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports.

`--repo <path>` names the repository whose jobs to read when the session is
rooted somewhere else — a multi-repo workspace root, say. Omit it and the
current working directory is used, exactly as before. Jobs are scoped to a
repo's own state dir, so this must match the repo the job was launched
against or it will not be found.

With no job id, cancels this session's single active job (an error if there
are none, or more than one). Terminates the job's process and transitions
its record to cancelled, so `/agy:status` shows it as cancelled rather than
running. Cancelling a job that has already finished or was already
cancelled is a clean no-op.
