---
description: Delegate a task to agy through a thin forwarding subagent (foreground or --background, fresh or --resume)
argument-hint: '[--repo <path>] [--background] [--resume|--fresh] [--model <model>] [--effort <effort>] [--timeout <seconds>] <task for agy>'
allowed-tools: Bash(python3:*), Agent
---

Invoke the `agy-delegate` subagent via the `Agent` tool, forwarding the raw
user request below as its prompt. The final user-visible response must be
the companion's output verbatim — do not paraphrase or summarize it.

Raw user request:
$ARGUMENTS

Execution mode:

- If the request includes `--background`, run `agy-delegate` in the
  background; it returns a job id immediately, without waiting for or
  rendering the run. Check progress later with `/agy:status`.
- Otherwise run it in the foreground and wait for its output.

Resume routing:

- If the request includes `--resume`, forward `--resume` — the companion
  resumes this repo's last delegate conversation, bind-checked against its
  log. A silent fallback to a new conversation is surfaced as an error,
  never rendered as a success.
- If the request includes `--fresh`, do not forward `--resume` — start a
  brand-new conversation (also the default).
- If neither is present, default to fresh unless the user's own phrasing
  clearly means "continue" (e.g. "keep going", "resume that", "continue
  what agy was doing", "pick up where it left off") — in that case forward
  `--resume`.

Operating rules:

- `agy-delegate` is a pure forwarder: it builds one command line invoking
  the companion's `delegate` subcommand and returns its output verbatim. Do
  not ask it to inspect the repo, poll `/agy:status`, fetch `/agy:result`,
  cancel a job, or summarize output.
- `--repo <path>` names the repository agy runs in. Forward it whenever the
  session is rooted outside the target repo — a multi-repo workspace root,
  say — resolving the user's shorthand (`@lab/foo`, `lab/foo`) to a real
  path first. Never guess a repo the user did not name: this run writes
  without confirmation. Omitted, the companion uses the current working
  directory, exactly as before.
- `--model`, `--effort`, and `--timeout` are optional passthroughs; forward
  them only when the user supplies them. `--timeout <seconds>` raises the
  companion's 300s foreground subprocess timeout ceiling for this one run;
  it is rejected together with `--background`, which has no ceiling.
- delegate always runs agy's default agent — never `--agent agy-review`.
- If the user did not supply a task, ask what agy should work on before
  invoking the subagent.

Security note: the companion always launches agy with
`--dangerously-skip-permissions --sandbox` (see README.md's `/agy:delegate`
section) — the delegated agent writes and runs commands in the target repo
(`--repo`, or the current working directory) without per-action confirmation. Nothing about that is configurable through this
command's arguments.
