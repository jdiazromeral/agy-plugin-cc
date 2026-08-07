---
description: Show the stored final output of a finished agy job for this repo
argument-hint: '[--repo <path>] [job-id]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" result $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports.

`--repo <path>` names the repository whose jobs to read when the session is
rooted somewhere else — a multi-repo workspace root, say. Omit it and the
current working directory is used, exactly as before. Jobs are scoped to a
repo's own state dir, so this must match the repo the job was launched
against or it will not be found.

With no job id, shows the most recently finished job for this repo. For a
review job, renders the finding table and verdict; for any other kind, or
when the stored output cannot be parsed as a review, renders the raw stored
text as-is. A job that is still running, silently fell back, or crashed is
reported as that state instead — never as an empty review.
