---
description: Run an adversarial agy review that argues against the change rather than validating it — steer it with free-text focus
argument-hint: '[--repo <path>] [--dry-run] [--background] [--scope auto|working-tree|branch] [--base <ref>] [--json] [--timeout <seconds>] [focus ...]'
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" adversarial-review $ARGUMENTS
```

Present the companion's output to the user verbatim — do not paraphrase or
summarize what it reports.

`--repo <path>` names the repository to act on when the session is rooted
somewhere else — a multi-repo workspace root, say. Omit it and the current
working directory is used, exactly as before. Resolve the user's shorthand
(`@lab/foo`, `lab/foo`) to a real path before passing it; do not guess a
repo the user did not name. It bounds agy's *writes* only — agy still walks
up to parent `AGENTS.md`/`CONTEXT.md` regardless of cwd.

This is a **read-only** review command: it never edits files, applies
patches, or proposes replacement code. Its job is to break confidence in the
change, not to validate it — it questions the chosen approach, its
assumptions, and where the design fails under real-world conditions (auth
and permissions, tenant isolation, data loss, rollback safety, race
conditions, schema drift, observability gaps), not just implementation
defects.

Without `--dry-run` or `--background`, this resolves the review target
(working tree or a branch diff, same selection as `/agy:review` minus
`--scope staged`), launches `agy` once as a blocking foreground run bound to
the vendored `agy-adversarial-review` agent, and renders the findings it
reports as a table with P0-P3 priorities alongside its overall
correctness/ship verdict. Return agy's output verbatim — do not paraphrase
or summarize a review.

With `--dry-run`, it resolves and sizes the review target and prints the
exact prompt that would be sent to `agy`, without invoking it.

With `--background`, it launches `agy` detached (writing to a persistent log
under this repo's state dir) and returns immediately, printing the job id
instead of waiting for and rendering the review. Check progress later with
`/agy:status`.

`--timeout <seconds>` raises the foreground subprocess timeout past its
300s default for this one run. Rejected together with `--background`,
which has no timeout ceiling.

Any free text after the flags is passed through as a **focus** the
adversarial agent weights heavily (e.g. `/agy:adversarial-review auth and
tenant isolation`) — it still reports any other material issue it finds,
not only the focus area.
