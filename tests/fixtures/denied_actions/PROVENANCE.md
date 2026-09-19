# Fixture provenance: denied_actions capture

One raw **event stream** capture from a real, authenticated **agy**, taken
against a throwaway scratch git repo under a temp dir — never
`lab/agy-plugin-cc`, never any worktree, never committed anywhere else.

This is the modernize-127 epic's one paid `agy` run spent on this mission
(M5), decided and spent by the mission orchestrator during preflight
research — **before any worker iteration ran** — and staged uncommitted at
`.looper/epics/modernize-127/M5_denied_actions_capture.ndjson` in the
primary repository for a worker to copy in verbatim. No dedicated
`tools/live_*_capture.py` script exists for this vector (unlike
`tests/fixtures/stream_events/`'s `tools/live_stream_events_capture.py`);
the full narrative is recorded in
`.looper/epics/modernize-127/records/M5_001_record_file.md` and the
contract's own "Provenance and the M4 interaction" section
(`M5_purpose.md`). What follows is transcribed from that record plus what
this capture's own bytes independently confirm structurally.

**Read this before anything else in this file**: this capture's bound agent
is `agy`'s own DEFAULT agent — the run passed no `--agent` flag at all.
It was deliberately NOT taken through `/agy:review`'s or
`/agy:adversarial-review`'s own command vector, because that vector cannot
produce a `denied_actions` entry today: both vendored review agents declare
`tools: []` (modernize-127 M4), so neither can attempt — and therefore
cannot have denied — a tool call. This capture proves `denied_actions` is
real, live, and parseable on the installed binary; it does not, and cannot,
demonstrate `/agy:review` itself producing one. See `M5_purpose.md`'s
"Provenance and the M4 interaction" section — this fixture's existence is
built entirely around that honesty constraint.

## `2026-09-20-run1.ndjson`

- **Date**: 2026-09-20 (see the mission record file's timestamp; this is
  the date the orchestrator's preflight research staged the file).
- **agy version**: 1.2.7 — the version installed and confirmed live for
  `denied_actions` by two zero-quota checks run first (`agy changelog`,
  and `strings` against the binary showing `json:"denied_actions,omitempty"`
  tied to `printmode.deniedActions` plus live soft-deny log format strings)
  — see `M5_purpose.md`.
- **Command vector** (reconstructed from the mission record; the flags
  independently confirmed by this capture's own bytes are called out
  below): `agy -p "<prompt>" --sandbox --new-project --output-format
  stream-json --log-file <path>`, run with `cwd` set to a throwaway scratch
  git repo under `/private/tmp`, `stdin` closed. No `--agent` flag. No
  `--dangerously-skip-permissions` — the soft-deny profile, deliberately
  not `/agy:delegate`'s own vector (which hard-codes
  `--dangerously-skip-permissions` and would auto-approve everything,
  producing no denial at all).
  - Structurally confirmed by the bytes themselves: the **init event**'s
    `init` object carries no `"agent"` key at all (same shape as
    `tests/fixtures/resume/PROVENANCE.md`'s no-`--agent`-passed capture) —
    positive evidence no `--agent` was requested, not merely an inference
    from the record. `init.permission_mode` reads `"request-review"`,
    matching every other `--sandbox`-without-`--dangerously-skip-
    permissions` capture in this repo (e.g.
    `tests/fixtures/stream_events/PROVENANCE.md`).
  - Not independently verifiable from the bytes alone (taken from the
    record as stated, not re-derived here): the exact prompt text. The
    record states the agent was asked to write a file to the scratch
    repo; the exact prompt string was not preserved verbatim in the
    record and is not reconstructed here rather than guessed.
- **Bound agent**: none requested — `agy`'s own default agent. See the
  structural confirmation above; this is the one fact this fixture's
  PROVENANCE must never blur (see the mission-wide caveat above).
- **Conversation UUID**: `3a3ca41e-a938-4a2d-a6bc-59912e50bdcc`.
- **Exit code**: 0 — `result.status` reads `"SUCCESS"` even though the
  run's one attempted tool call (`write_to_file`) was denied. This matches
  the `agy` changelog's "denials are not fatal" fix, and is itself the
  finding that motivates this mission's rendering work: `result.response`
  is the empty string (the run ended right after the denial, with no
  further model turn), so an empty response with a hidden denial is the
  most innocuous possible failure to render as "nothing to report" without
  this mission's plumbing.

### Lines

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id`, `init.cwd` (redacted, see Scrubbing), `init.tools` (57 entries — `agy`'s full default tool baseline, consistent with the default agent, not either vendored `tools: []` review agent), `init.permission_mode` (`"request-review"`); no `init.agent` key |
| 2 | `step_update` | `step_index` 0, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 1, `step_type` `"agent_response"`, `state` `"DONE"`, `duration_seconds`, `usage` |
| 4 | `step_update` | `step_index` 2, `step_type` `"tool"`, `tool_name` `"write_to_file"`, `state` `"ACTIVE"`, `tool_info.parameters.TargetFile` |
| 5 | `step_update` | `step_index` 2, `step_type` `"tool"`, `tool_name` `"write_to_file"`, `state` `"ERROR"`, `tool_info.error` (`type` `"TOOL_ERROR"`, `message` — the soft-deny text: `permission check failed for write_file "..." : user denied permission for write_file(...)`) |
| 6 | `result` | `conversation_id`, `status` `"SUCCESS"`, `response` (empty string), `duration_seconds`, `num_turns` 1, `usage`, **`denied_actions`**: `[{"action": "write_file", "display_name": "WriteToFile"}]` |

**Event counts by type**: 1 `init`, 4 `step_update`, 1 `result`.

This is the first committed capture whose **step update** sequence includes
a `"tool"` **step type** with `state: "ERROR"` — every prior real capture in
this repo either never attempted a tool call (`tools: []` on both review
agents) or, for `agent_fallback`, used an unfamiliar step type without an
error. `tool_info.error.message` also carries a durable, live-observed
soft-deny wording this repo had not captured before: `"Do not attempt to
circumvent this denial by rephrasing the command, using alternative
tools/scripts (e.g. python, sh, curl), or accessing the same target
resource. Proceed without performing this action."` — not asserted by any
test in this mission (out of scope: the mission's parser reads
`result.denied_actions`, not per-step tool-error text), recorded here only
because it is real and undocumented elsewhere.

## Scrubbing

The only edits applied are path redaction, matching the discipline already
used in `tests/fixtures/stream_events/PROVENANCE.md` and
`tests/fixtures/delegate/PROVENANCE.md`: the scratch repo's absolute path
(which embedded a machine username, a workspace path segment, and a
capturing-session UUID) was rewritten to
`/private/tmp/claude-REDACTED/-REDACTED-WORKSPACE/REDACTED-SESSION/scratchpad/...`
in `init.cwd` and in the two `tool_info.parameters.TargetFile` /
`tool_info.error.message` occurrences (the denied write target path).
Every other byte — every event, every field, the timing, the `usage`
block, the `denied_actions` entry itself — is exactly as `agy` wrote it.
No credentials or real email addresses appear in this fixture (checked).
