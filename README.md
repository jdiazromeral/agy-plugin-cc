# agy-plugin-cc

Use the **Antigravity CLI (`agy`)** from inside Claude Code — for code review, or
to delegate tasks and track them in the background.

> **Status: pre-alpha (v0.3.0).** All seven commands below are implemented and covered
> by an offline test suite (410 tests against a fake `agy` on `PATH`, never
> the real binary — `make check` prints the authoritative count).
>
> **All seven commands have been run end to end against a real, authenticated
> `agy`, re-verified on 1.2.6** (2026-09-19, via `make check-live` / `agy_companion.py setup`):
> `/agy:review` and `/agy:adversarial-review` producing schema-conforming
> findings; `/agy:delegate` fresh, `--resume`, and `--background`;
> `/agy:status` against a job both mid-flight and finished; `/agy:result`
> harvesting a real stored result; and `/agy:cancel` killing a real
> mid-stream process, verified dead by PID. Includes Claude Code session
> lifecycle hooks (`SessionStart`/`SessionEnd`) and native `evals/` test cases.
> The evidence is committed, not asserted — see `docs/review-schema-verdict.md`
> and the captures under `tests/fixtures/`, each with its own PROVENANCE note.
>
> Re-verify after any `agy` upgrade with `make check-live` (spends one trivial
> live run) or `make check-live-free` (spends nothing).

This is a port of [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc)
(Apache-2.0) from Codex to Antigravity.

## Installation

1. Install and authenticate the [Antigravity CLI](https://antigravity.google/docs/cli/)
   (`agy`) — version **1.1.11 or newer**. `/agy:setup` (below) verifies this for you.
2. In Claude Code, add this repository as a plugin marketplace and install the
   plugin from it:
   ```
   /plugin marketplace add jdiazromeral/agy-plugin-cc
   /plugin install agy@agy
   ```
3. Run `/agy:setup` to confirm agy is installed, authenticated, and that the
   vendored review agent actually binds (see below).

No `pip install` or other dependency step — the companion is Python 3
standard library only.

### `${CLAUDE_PLUGIN_ROOT}` and the plugin cache, settled

Two facts about how the `/plugin marketplace add` + `/plugin install` flow
above actually executes commands, established while chasing live install
defects and recorded here so they aren't re-derived:

- **`${CLAUDE_PLUGIN_ROOT}` resolves to this repo's `plugins/agy`
  directory, not to any cache.** `/plugin marketplace add
  jdiazromeral/agy-plugin-cc` sets up a *directory marketplace*, per
  `.claude-plugin/marketplace.json`'s `"source": "./plugins/agy"` — Claude
  Code runs commands live against that path in the repo. A version-keyed
  cache does also exist, at
  `~/.claude/plugins/cache/agy/agy/<version>/`, but it is not in the
  command execution path for a directory marketplace like this one.
- **The cache is real and IS version-keyed**, off
  `.claude-plugin/marketplace.json`'s `metadata.version` /
  `plugins[].version` (currently `0.3.0`). `claude plugin marketplace
  update` alone will **not** refresh a stale cache while that version
  string stays the same — only `/plugin uninstall` followed by `/plugin
  install` does. This is a note about install/update mechanics for anyone
  debugging a plugin that seems to be running stale code from the cache;
  it is not a claim that command execution itself goes through the cache
  for this repo's directory-marketplace setup (it doesn't — see above).

## Commands

| Command | What it does |
|---|---|
| `/agy:setup` | Check that `agy` is installed, authenticated, which global agents are registered, and whether the vendored `agy-review` agent actually binds |
| `/agy:review` | Resolve a review target (working tree, staged, or branch diff), run it through `agy-review`, and render findings as a P0-P3 table with an overall verdict — or preview the prompt with `--dry-run` |
| `/agy:adversarial-review` | Same review target selection and launch path as `/agy:review`, but bound to a skeptical `agy-adversarial-review` agent that tries to break confidence in the change rather than validate it; steerable with free-text focus. Read-only, same P0-P3 finding table |
| `/agy:delegate` | Hand a task to `agy`'s default agent through a thin forwarding subagent, foreground or `--background`, fresh or `--resume`. **Write-capable with no per-action confirmation** (`--dangerously-skip-permissions`) — see the section below before using it |
| `/agy:status` | Show active and recent `agy` jobs for this repo, derived from their persistent logs — including a Stall column (a running job gone quiet too long) and a Usage column (token counts once a job has finished) |
| `/agy:result` | Print the stored final output of a finished job |
| `/agy:cancel` | Cancel a running background job |

### Targeting a repository: `--repo <path>`

Every repo-scoped command (`review`, `adversarial-review`, `delegate`,
`status`, `result`, `cancel`) resolves its repository from the current
working directory by default. Pass `--repo <path>` to name one explicitly —
necessary whenever the session is rooted somewhere that is not itself a git
repo, such as a workspace root holding many repos. `setup` does not take it:
it checks the `agy` install, not a repository.

The repository is the unit of scoping throughout: `agy` runs with its cwd
set there, and the job **state dir** is keyed on the repo root, so
`/agy:status`, `/agy:result` and `/agy:cancel` must be pointed at the same
repo the job was launched against. As with `--add-dir`, this bounds `agy`'s
*writes*; it does not bound its reads, since `agy` walks up to parent
`AGENTS.md`/`CONTEXT.md` regardless of cwd.

Deferred to v2: the Stop review-gate hook. Not planned: `/transfer` — Codex
does it with a protocol-level `externalAgentConfig/import` that `agy` has no
equivalent for.

See `docs/examples/` for two runnable walkthroughs — a real bug caught by
`/agy:review`, and what a failed agent bind looks like — both built from
committed fixture captures, not fabricated output.

### `/agy:setup`

Run this first, and again after upgrading `agy`. It reports:

- whether `agy` is installed and its version;
- best-effort authentication state (`agy` 1.1.6 has no first-class
  auth-status API, so this is inferred from `agy agents`' exit code and
  stderr wording — see `AGENTS.md`);
- **global** custom agents `agy agents` lists (an empty list is legitimate,
  not an error);
- whether the **vendored** `agy-review` agent (shipped at
  `plugins/agy/agents/agy-review/agent.md`, workspace-scoped, so it never
  appears in the `agy agents` listing above) actually **binds**. This uses a
  free, zero-quota probe (`agy -p ... --agent agy-review --model
  <deliberately-invalid> --sandbox --new-project --log-file <path>`,
  verified against the log, never stdout or the exit code — see
  `docs/review-schema-verdict.md` Finding A) and tells you plainly whether
  `/agy:review` will work. The probe runs in a staged **agent workspace**,
  the same way `/agy:review` itself does, so its answer is about the command
  you are about to run rather than about whatever directory you happened to
  run `/agy:setup` from. If it reports **not bound**, the plugin install is
  incomplete — check that `plugins/agy/agents/agy-review/agent.md` is present,
  and that your `agy` version supports `--new-project` and `--add-dir`
  (verified against 1.1.6+ and 1.1.8);
- the effective model and effort agy actually resolved to, and a quota
  summary — both read back from the same class of free, zero-quota
  read-only-slash-command probe used for the bind check above, so this is a
  proven answer rather than an assumption from the flags you passed.

### `/agy:review [--dry-run] [--background] [--scope ...] [--base <ref>] [--json]`

Resolves a review target (auto-detects working-tree changes vs. a branch
diff, or pass `--scope staged`/`--scope branch --base <ref>` explicitly),
sizes the change, and sends the diff to `agy` bound to the `agy-review`
agent. Renders findings as a table with `[P0]`-`[P3]` priorities and an
overall correctness verdict. `--dry-run` previews the prompt without
invoking `agy`. `--background` launches detached and returns a job id
immediately — check it with `/agy:status`, harvest it with `/agy:result`.

### `/agy:adversarial-review [--dry-run] [--background] [--scope ...] [--base <ref>] [--json] [focus ...]`

A second, steerable review command whose posture is to argue against the
change rather than validate it — "break confidence in the change, not
validate it," in upstream's framing. It reuses `/agy:review`'s target
selection (auto-detect, `--scope working-tree`, or `--scope branch --base
<ref>`; unlike `/agy:review`, it does not support `--scope staged`), launch
path, **bind** check, and finding-table rendering unchanged, but sends the
diff to `agy` bound to a different vendored agent,
`agy-adversarial-review`, whose system prompt is deliberately skeptical: it
prioritizes auth/permissions/tenant isolation, data loss, rollback safety,
race conditions, schema drift, and observability gaps over ordinary
implementation defects, and reports only material findings. Free text after
the flags is passed through as a **focus** the agent weights heavily without
narrowing its search to only that area, e.g.:

```
/agy:adversarial-review auth and tenant isolation
```

It is **read-only**, exactly like `/agy:review` — no edits, no PR fixes, no
proposed patches. `--dry-run` and `--background` behave the same as they do
for `/agy:review`.

### `/agy:delegate [--background] [--resume|--fresh] [--model <m>] [--effort <e>] <task>`

Hands `<task>` to `agy`'s default agent (never `--agent agy-review` — that's
review's job) through a thin forwarding subagent, write-capable, in this
repo. `--resume` continues this repo's last delegate conversation instead of
starting fresh; `--background` returns a job id immediately.

**What "write-capable" actually means:** delegate always launches with
`agy -p <task> --dangerously-skip-permissions --sandbox ...`. `--sandbox`
alone (`/agy:review`'s profile) never writes anything — it soft-denies every
tool-call confirmation, so the agent can only produce text. Delegate adds
`--dangerously-skip-permissions` on top specifically to make it write-capable:
that flag bypasses `agy`'s own per-action confirmation gate entirely, so the
delegated agent can read, write, and run commands in this repo **without
asking you to approve each one** — there is no per-step review loop the way
there is in an interactive `agy` session. This is a fixed, always-on profile;
there is no flag or setting to run delegate with confirmations re-enabled.
`--sandbox` stays present alongside it, but what it still restricts once
`--dangerously-skip-permissions` is also passed has not been independently
verified against a real destructive action in this repo's own captures — the
live fixtures in `tests/fixtures/delegate/` only exercise a trivial
no-tool-use prompt. Treat `/agy:delegate` as equivalent to handing an
unattended agent full write access to the current repo, exactly like
`agy`'s own `--dangerously-skip-permissions` flag warns.

**Read this before relying on `--resume`:** `agy --conversation <uuid>`
genuinely resumes when the uuid is known, but **silently falls back** to a
brand-new conversation when it is not — exit 0, clean stdout, empty stderr,
with the only trace in `agy`'s own `--log-file`. Both halves of that are
live-verified on 1.1.8 (see `tests/fixtures/delegate/PROVENANCE.md`): a
genuine resume recalled a word from its prior turn, and an unknown uuid
produced the fallback trace with no outward signal. This plugin guards against
that by reading the log for the resume's actual bind before ever rendering
output as a successful resume: a silent fallback is surfaced as a distinct
error, and the wrong new conversation it created is never recorded as this
repo's delegate conversation (so a later `--resume` doesn't compound the
mistake). You will see an explicit "SILENT FALLBACK" error rather than a
delegate reply that quietly came from a fresh conversation.

### `/agy:status [--json] [--all-sessions]`

Reads this repo's state dir and renders active/recent jobs — kind, status,
conversation id, elapsed time, whether the job is stalled (a `running` job
with no new **event stream** activity for too long — see "No babysitter
process" below), token usage once a **result event** has landed, and a log
tail — scoped to the current Claude session by default (`--all-sessions`
shows every job for the repo).

### `/agy:result [job-id]`

Prints the stored final output of a finished job (defaults to the most
recently finished one for this repo). A review job renders as the finding
table; any other kind (or unparseable review output) renders as raw text. A
job that is still running, silently fell back, or crashed is reported as
that state — never as an empty or fabricated result.

### `/agy:cancel [job-id]`

Terminates a running background job by its recorded PID and marks it
`cancelled`, so `/agy:status` reflects that from then on. Cancelling an
already-finished or already-cancelled job is a clean no-op. With no job id,
cancels this session's single active job (an error if there are zero or
more than one).

It **verifies the process actually died** before reporting success: SIGTERM,
then up to 5s for a clean exit (a real `agy` takes 0.16s), then SIGKILL. If
the process somehow survives both, the command fails and deliberately leaves
the job as `running` rather than marking it `cancelled` — a `cancelled` job
is hidden from `/agy:status` from then on, so claiming an unverified kill
would make a still-running, still-quota-spending job invisible.

## Requirements

- Antigravity CLI (`agy`) **1.1.11+**, installed and authenticated. Binaries
  older than 1.1.10 accept `--model` / `--effort` on a headless `-p` run and
  then ignore them, so `/agy:delegate` would silently use a different model
  than you asked for. 1.1.11 closes the remaining gap: it added a free,
  zero-quota probe (a read-only slash command answered without starting an
  agent turn) that reflects back the model/effort a run actually resolved
  to, so this plugin can **prove** `--model`/`--effort` took effect instead
  of only assuming it from the version string. This repo supports one agy
  binary at a time with no dual code path for an older one, so the floor
  moves to wherever the stronger guarantee is available rather than staying
  at the minimum that merely fixed the bug.
- Python 3.9+ (standard library only — no dependencies, no install step)

## Testing

Three tiers, because they catch genuinely different things.

| Command | Cost | Catches |
|---|---|---|
| `make check` | free, hermetic, ~11s | ordinary regressions. Runs in CI on every push. Drives a fake `agy`; never touches the real binary or the network |
| `make check-live-free` | free, needs an authenticated `agy` | drift in the two traces that resolve *before* any model call — the agent **bind** and the resume **silent fallback** |
| `make check-live` | spends a few trivial live runs | everything above, plus the full background **job** lifecycle driven through the shipped commands: launch → `status` → `result`, and a `cancel` that asserts the process is genuinely **dead** |

**Run `make check-live` after every `agy` upgrade.** This is not boilerplate
caution. The offline suite drives `tests/fake_agy.py`, and a fake is only as
good as the last time somebody checked it against the binary — that gap has
hidden three shipped defects so far: an agent that could never bind, a
fallback guard matching only presentation text, and a `cancel` that reported
kills it never verified. Each was invisible to a green offline suite.

`tests/test_log_fidelity.py` is the standing counterweight: it holds every
log pattern against real captured `agy` output in `tests/fixtures/`, so the
fake can no longer be validated against itself.

## Development

This plugin was built with [looper](https://github.com/jdiazromeral/looper),
which leaves two kinds of artifact under `.looper/`. They are treated
differently on purpose:

- **`.looper/knowledge/glossary.md` is tracked, and is normative.** It is the
  project's ubiquitous-language contract — the same word for the same concept
  in code, tests, and user-facing output, with the tempting-but-wrong synonym
  named explicitly. `AGENTS.md` requires it, and much of the code comments
  against it. Read it before naming anything.
- **`.looper/epics/` is not tracked.** Mission contracts, per-iteration worker
  records and epic journals are development evidence rather than
  documentation, so they stay local; running a mission here recreates the
  directory.

Comments and docstrings here state the rule and the reason for it, never when
or by whom it was changed — that is what `git log` is for. If you find prose
that reads as changelog, it is a defect; rewrite it as the constraint it was
trying to protect.

## How it differs from the Codex plugin

Codex's plugin is a JSON-RPC client for a long-lived `codex app-server`. `agy`
has no such surface, so this plugin drives the CLI directly: detached `agy -p`
processes, a per-repo state directory, and job progress read out of each run's
`--log-file`. That produces a coarser fidelity than upstream in a few
concrete ways:

- **Job status is coarse.** There is no phase-by-phase progress narration —
  only running / completed / a distinct error state (silent fallback, or
  bound-but-crashed), derived by parsing the log for known trace lines.
- **No babysitter process.** A background job's exit code is never captured
  by any later Claude Code turn, so there is still no exit code to consult.
  `/agy:status` now layers a **stall** signal on top of the coarse
  running/completed/error status, though: a `running` job whose `output_file`
  has gone quiet (no new **event stream** line, the same signal a
  **step update** would produce) for longer than a threshold is reported
  stalled, with how long. This does not replace the coarse status — a
  stalled job still reads `running` so `/agy:cancel` can still find and
  terminate it by PID — it is a distinct, additional signal for telling a
  genuinely stuck job apart from a slow-but-fine one, which previously read
  identically.
- **No `/transfer`.** Not planned — see above.

See `AGENTS.md` for the verified findings behind those constraints, and
`docs/review-schema-verdict.md` for the live-fixture evidence behind the
review path specifically.

## License

**AGPL-3.0-or-later** — see [`LICENSE`](LICENSE).

The two vendored review prompts under `plugins/agy/agents/` were obtained
under Apache-2.0 and remain available under those terms from their upstream
sources; Apache-2.0 is one-way compatible with (A)GPLv3, so the combined work
here is AGPL. See [`NOTICE`](NOTICE) for the full picture.

## Attribution

Architecture, command surface, and job model derive from `openai/codex-plugin-cc`
(Apache-2.0). The code-review prompt derives from OpenAI's Codex review prompt
(`openai/codex`, Apache-2.0), verified against current upstream at
`codex-rs/prompts/templates/review/rubric.md`. See `NOTICE` for details, and
each vendored agent's own PROVENANCE note for the per-file evidence.
