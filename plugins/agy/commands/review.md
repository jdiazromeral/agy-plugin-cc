---
description: Review a target (working tree, staged, or branch diff) with agy, or preview the prompt with --dry-run
argument-hint: '[--repo <path>] [--dry-run] [--background] [--scope auto|working-tree|staged|branch] [--base <ref>] [--json] [--timeout <seconds>]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" review $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports.

`--repo <path>` names the repository to act on when the session is rooted
somewhere else — a multi-repo workspace root, say. Omit it and the current
working directory is used, exactly as before. Resolve the user's shorthand
(`@lab/foo`, `lab/foo`) to a real path before passing it; do not guess a
repo the user did not name. It bounds agy's *writes* only — agy still walks
up to parent `AGENTS.md`/`CONTEXT.md` regardless of cwd.

Without `--dry-run` or `--background`, this resolves the review target
(working tree, staged changes, or a branch diff), launches `agy` once as a
blocking foreground run bound to the vendored `agy-review` agent, and
renders the findings it reports as a table with P0-P3 priorities alongside
its overall correctness verdict. Return agy's output verbatim — do not
paraphrase or summarize a review.

With `--dry-run`, it resolves and sizes the review target and prints the
exact prompt that would be sent to `agy`, without invoking it.

With `--background`, it launches `agy` detached (writing to a persistent
log under this repo's state dir) and returns immediately, printing the job
id instead of waiting for and rendering the review. Check progress later
with `/agy:status`.

`--timeout <seconds>` raises the foreground subprocess timeout past its
300s default for this one run. Rejected together with `--background`,
which has no timeout ceiling.
