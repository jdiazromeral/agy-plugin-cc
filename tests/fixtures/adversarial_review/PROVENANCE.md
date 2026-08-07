# Fixture provenance: adversarial_review captures

The first live capture of `agy-adversarial-review` ever taken against a real,
authenticated **agy** — this agent had never been run against a real binary
before this capture. One raw **event stream**
capture (`--output-format stream-json`, no `--json-schema`, per this epic's
settled verdict — `docs/json-schema-verdict.md`), taken against a throwaway
scratch git repo under the session scratchpad — never `lab/agy-plugin-cc`,
never committed anywhere else. Captured by an ad-hoc, uncommitted script
(not a new `tools/` file, per the mission's contract) that reuses
`tools/live_review_capture.py`'s `bootstrap_scratch_repo()` unmodified, plus
a second agent-staging step (`.agents/agents/agy-adversarial-review/agent.md`
copied in alongside `agy-review`'s), and mirrors
`companion/adversarial_review.py`'s own `_REVIEW_INSTRUCTIONS_TEMPLATE` and
`assemble_prompt()` output exactly (no `focus` argument supplied) so the
prompt text matches byte-for-byte what a real `/agy:adversarial-review`
invocation against this exact diff would send.

- **Date**: 2026-07-30
- **agy version**: 1.1.8
- **Command**:
  ```
  agy -p "<prompt below>" --agent agy-adversarial-review --sandbox \
      --new-project --output-format stream-json --log-file <path>
  ```
  run with `cwd` set to the scratch repo root, `stdin` closed
  (`subprocess.DEVNULL`), `--json-schema` deliberately omitted.
- **Prompt** (the diff is embedded directly in the prompt text; `--sandbox`
  soft-denies tool calls in headless print mode for *file edits*, but read
  tools like `find_by_name`/`view_file` are still available and this run
  used them — see "Step updates" below):
  ```
  Review target: working tree diff
  Files touched: 1
  Insertions: +1
  Deletions: -1

  Adversarially review the following diff, per your system instructions:
  actively try to disprove that it is safe to ship, not just check it for
  bugs. Output only the JSON described in your system instructions, nothing
  else.

  ```diff
  diff --git a/calc.py b/calc.py
  index b2db1f1..79278a0 100644
  --- a/calc.py
  +++ b/calc.py
  @@ -1,3 +1,3 @@
   def add(a, b):
       """Add two numbers."""
  -    return a + b
  +    return a - b  # bug: should be a + b
  ```
  ```
  Same seeded one-line bug `tools/live_review_capture.py`'s
  `bootstrap_scratch_repo()` has staged for every review-schema capture so
  far (M2's own probe, the two 2026-07-24 `tests/fixtures/review/` fixtures,
  and all four of `docs/json-schema-verdict.md`'s runs) — reused deliberately
  rather than inventing a new bug, so the only new variable this capture
  introduces is the agent.
- **Bound agent**: `agy-adversarial-review` — the **init event**'s
  `init.agent` field reads `"agy-adversarial-review"` directly (same
  structural bind proof M2's `tests/fixtures/stream_events/PROVENANCE.md`
  used), and the `--log-file` contains a `Created conversation` line with no
  `Agent "agy-adversarial-review" not found, falling back to default` line —
  confirmed bound two ways, not one.
- **Conversation UUID**: `5a26513d-be46-4877-b190-f9417797f32d`
- **Exit code**: 0
- **This capture carries a finding** — the explicit reason it was taken:
  a prior live `/agy:review` run on 2026-07-30 (see `AGENTS.md`'s settled
  findings) returned `No findings.` with
  a correct verdict, so the P0–P3 findings-table render path had never
  executed against real output before this fixture. This run's `result`
  event's `response` parses as one JSON object with a single `P0` finding
  (`"[P0] Fix incorrect subtraction operator in add function"`,
  `overall_correctness: "patch is incorrect"`), reusing the same
  `bootstrap_scratch_repo()` seeded bug that reliably produces exactly one
  finding across every prior capture that has used it.
- **Step updates**: 11 (vs. M2's plain `agy-review` capture's 5) — this
  agent's more adversarial posture led it to actually call `find_by_name`
  (locate `calc.py`) and `view_file` (read it) before responding, twice each
  (`ACTIVE` then `DONE`), plus three `agent_response` steps and one
  `checkpoint`. `--sandbox` soft-denies *write*/edit tool confirmations in
  headless print mode (`docs/review-schema-verdict.md` Finding C); it does
  not block read-only tools, and this run demonstrates that concretely: no
  denial, no fallback, no missing find_by_name/view_file *DONE* event with
  an empty output.
- **Scrubbing**: the only edit applied is path redaction, identical in kind
  to `tests/fixtures/stream_events/PROVENANCE.md`'s discipline — the scratch
  repo's absolute path (which embedded this machine's username and the
  capturing session's uuid) appears seven times across this capture (the
  **init event**'s `init.cwd`, both `find_by_name` tool-call events'
  `SearchDirectory` parameter, both `view_file` tool-call events'
  `AbsolutePath` parameter, and the **result event**'s
  `response.findings[0].code_location.absolute_file_path`) and every
  occurrence was rewritten identically:
  `/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>` ->
  `/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION`. Every
  other byte — every event, every field, the finding text, `usage`, timing —
  is exactly as `agy` wrote it. No credentials or emails appear in this
  fixture (checked); the `--log-file`'s own auth-noise lines (the documented
  "error getting token source" startup noise, harmless per `AGENTS.md`) are
  a separate file, never committed here — only the NDJSON stdout is.

## `2026-07-30-run1.ndjson`

13 lines, one JSON object per line:

| # | `event` | `step_type` | notes |
|---|---|---|---|
| 1 | `init` | — | `conversation_id`, `init.cwd` (redacted), `init.agent` (`"agy-adversarial-review"`), `init.tools`, `init.permission_mode` |
| 2 | `step_update` | `user_input` | `step_index` 0, `state` `"DONE"` |
| 3 | `step_update` | `agent_response` | `step_index` 1, `state` `"DONE"`, `usage` |
| 4 | `step_update` | `tool` (`find_by_name`) | `step_index` 2, `state` `"ACTIVE"` |
| 5 | `step_update` | `tool` (`find_by_name`) | `step_index` 2, `state` `"DONE"`, `output: "calc.py"` |
| 6 | `step_update` | `checkpoint` | `step_index` 3, `state` `"DONE"`, `usage` |
| 7 | `step_update` | `agent_response` | `step_index` 4, `state` `"DONE"`, `usage` |
| 8 | `step_update` | `tool` (`view_file`) | `step_index` 5, `state` `"ACTIVE"` |
| 9 | `step_update` | `tool` (`view_file`) | `step_index` 5, `state` `"DONE"`, `output: "4 lines, 83 bytes"` |
| 10 | `step_update` | `agent_response` | `step_index` 6, `state` `"ACTIVE"`, partial `text_delta` |
| 11 | `step_update` | `agent_response` | `step_index` 6, `state` `"ACTIVE"`, more `text_delta` |
| 12 | `step_update` | `agent_response` | `step_index` 6, `state` `"DONE"`, final `text_delta`, `usage` |
| 13 | `result` | — | `conversation_id`, `status` `"SUCCESS"`, `response` (one `P0` finding), `duration_seconds`, `num_turns` 1, `usage` |

**Event counts by type**: 1 `init`, 11 `step_update`, 1 `result`.

`result.response` parses cleanly as one JSON object with all four
`agy-adversarial-review` schema keys (`findings`, `overall_correctness`,
`overall_explanation`, `overall_confidence_score`) — the same schema
`agy-review` uses (`plugins/agy/agents/agy-adversarial-review/agent.md`'s own
PROVENANCE block states this deliberately, and
`tests/test_adversarial_review.py`'s `test_schema_stays_in_lockstep_with_agy_review`
enforces it stays that way). `code_location.absolute_file_path` is a
genuine absolute path (post-redaction) in this run, matching run3's
deviation-free shape rather than run4's relative-path deviation
(`tests/fixtures/review/2026-07-24-run4.provenance.md`).

`tests/test_adversarial_review.py`'s `BoundAdversarialReviewFromRealFixtureTest`
replays this fixture verbatim as the fake agy's stdout (via
`FAKE_AGY_STREAM_PATH`, distinct from `FAKE_AGY_STDOUT_PATH` — see
`tests/fake_agy.py`'s docstring) and asserts the rendered CLI output shows
the `P0` finding row and the `"patch is incorrect"` verdict, end to end
through the real, migrated `_run_live_review` -> `parse_event_stream` ->
`EventStream.response` -> `tolerant_parse` -> `render_review` path — the
first time the P0–P3 findings-table render path has ever executed against
real captured output (every prior fixture-driven test used either a hand
description of the schema or `review_bound_valid`'s synthetic wrap, and the
one prior real end-to-end `/agy:review` run returned a clean,
empty-findings verdict).
