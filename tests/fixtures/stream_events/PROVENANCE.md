# Fixture provenance: stream_events captures

One raw **event stream** capture from a real, authenticated **agy**, taken
against a throwaway scratch git repo under a temp dir — never
`lab/agy-plugin-cc`, never any worktree, never committed anywhere else.
Captured by `tools/live_stream_events_capture.py`, reusing
`tools/live_review_capture.py`'s `bootstrap_scratch_repo()` (same scratch-repo
+ workspace-scoped `agy-review` agent + one-line seeded bug pattern already
used for `tests/fixtures/review/`), with `--output-format stream-json` and
no `--json-schema`.

## re-capture against agy 1.1.9 (2026-08-02)

The original capture below (`2026-07-30-run1.ndjson`, now removed) was taken
against agy **1.1.8**. a later capture re-captured this
exact vector against the real 1.1.9 binary on `PATH`, to check honestly
whether `agy changelog`'s one 1.1.9 stream-relevant note — the headless
`stream-json` **init event** no longer advertises tools absent from the
build — actually moved the captured bytes, rather than trusting the
free `init.tools`-count comparison the orchestrator did from already-committed
bytes (59 old vs. 56 on the already-1.1.9 `agent_fallback` fixture) as
decisive on its own.

- **Date**: 2026-08-02
- **agy version**: 1.1.9
- **Command**:
  ```
  agy -p "<prompt below>" --disable-slash-commands --agent agy-review \
      --sandbox --new-project --output-format stream-json --log-file <path>
  ```
  run with `cwd` set to a throwaway scratch git repo under a temp dir,
  `stdin` closed (`subprocess.DEVNULL`), `--json-schema` deliberately
  omitted (out of scope per the epic preamble and
  `docs/json-schema-verdict.md`'s verdict).
- **Prompt** (the diff is embedded directly in the prompt text; `--sandbox`
  soft-denies tool calls in headless print mode):
  ```
  Review the following working tree diff for bugs. Output only the JSON
  described in your system instructions, nothing else.

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
- **Bound agent**: `agy-review` — the **init event**'s `init.agent` field
  reads `"agy-review"` directly.
- **Conversation UUID**: `a4425612-2b6c-4e0c-a9b5-e7600418be81`
- **Exit code**: 0

### Did the 1.1.9 stream shape move? Reported plainly.

**Key set held.** The **init event**'s key set is exactly `{"agent", "cwd",
"permission_mode", "tools"}`, identical to the 1.1.8 capture. The **result
event**'s key set is exactly `{"conversation_id", "status", "response",
"usage", "num_turns", "duration_seconds"}`, also identical.
`tests/test_log_fidelity.py`'s `test_init_event_field_names_and_bound_agent`
was correctly scoped — it asserts the key set, not tool contents, so it
stayed green across the version bump because the key set genuinely did not
move; only the CONTENTS of one key did.

**Event-kind sequence held.** `init` -> `step_update`(xN) -> `result`, same
as before.

**Step-type vocabulary held.** `{"user_input", "agent_response",
"checkpoint"}`, the same three types the 1.1.8 capture showed — no new step
type appeared on this vector (contrast `tests/fixtures/agent_fallback/`,
whose default-agent run showed a fourth, `"unknown"`).

**`init.tools` count moved, exactly as the changelog said.** 59 (1.1.8) ->
**56** (1.1.9), for the identical `agy-review` agent and the identical
prompt shape — a controlled, real confirmation of `agy changelog`'s 1.1.9
note, not an inference from comparing different agents across different
runs the way the orchestrator's free preflight check necessarily was. The
**actual tool names removed** were not diffed byte-for-byte against the old
fixture (out of this mission's scope — the changelog explains the count
delta and no consumer in this codebase reads `init.tools` contents), but the
count delta (-3) is consistent with "tools absent from the build no longer
advertised."

**Step count is NOT structurally pinned and differs (5 -> 4), for a
non-structural reason.** The 1.1.8 capture's `agent_response` step arrived
as three lines (`ACTIVE`, `ACTIVE`, `DONE` — two partial `text_delta`
chunks then the final one). This 1.1.9 capture's `agent_response` step
arrived as two lines (`ACTIVE`, `DONE` — one partial chunk then the final
one). Both shapes are consistent with the parser's documented assumption
("a step update does not always arrive in more than one line") — this is
streaming-chunk-count variance, not a schema change, and
`test_step_update_field_names`'s asserted `step_update_keys` union (`
{"conversation_id", "step_index", "step_type", "state", "text_delta",
"duration_seconds", "usage"}`) is unchanged between the two captures.

**`permission_mode` held**: `"request-review"` in both captures (this
capture, and the 1.1.8 original) — `--dangerously-skip-permissions` is not
part of this command vector, only `review._agy_command`'s vector passes it,
and this tool deliberately mirrors the *review* vector, not the delegate
one.

**Verdict: the 1.1.9 stream shape did NOT structurally move for this
vector.** The one real change is `init.tools`'s content (fewer entries,
matching the changelog), which no shipped consumer reads. Everything this
codebase's parser (`stream_events.parse_event_stream`) and consumers
(`status.derive_status`, `delegate._resume_bind_check`) actually depend on
— key sets, event-kind sequence, step-type vocabulary, `result.status` —
held exactly.

### Piece 5 the "Starting new conversation (agent=...)" line

This run's `--log-file` (not committed, quoted here for the record) reads:

```
I0802 ... conversation_manager.go:374] Starting new conversation (agent=true)
```

`agent=true` on a run whose `--agent agy-review` was requested AND
genuinely resolved (confirmed independently by `init.agent` reading back
`"agy-review"` and a `Created conversation <uuid>` line present with no
fallback line). Compare against
`tests/fixtures/agent_fallback/PROVENANCE.md`'s companion entry, which reads
`agent=false` on a run whose `--agent` was requested but did NOT resolve.
**Together these two lines are the positive bind proof the epic could not
otherwise find**: the boolean tracks whether the agent actually resolved,
not merely whether `--agent` was passed on argv (both runs passed
`--agent`; the values differ). See the mission's record file for the full
writeup — this is recorded as a finding for a follow-up mission to build
on; this capture does not implement anything on top of it (`review._bind_check`,
`setup.py`'s doctor, and `PRINTMODE_RE`'s retirement are already-finished
work and out of scope here).

## Scrubbing

The only edit applied is path redaction, in the **init event**'s
`init.cwd` field — the scratch repo's absolute path (which embedded this
machine's username and the capturing session's uuid) was rewritten:
`/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>/scratchpad/...`
-> `/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/scratchpad/...`,
matching the redaction discipline already used in
`tests/fixtures/delegate/PROVENANCE.md` (`/Users/<user>` ->
`/Users/REDACTED`, workspace/session path segments -> placeholders). Every
other byte — every event, every field, the finding text, `usage`, timing —
is exactly as `agy` wrote it. No credentials or emails appear in this
fixture (checked): `--log-file`'s own auth noise is a separate file, never
committed here — only the NDJSON stdout is.

## `2026-08-02-run1.ndjson`

6 lines, one JSON object per line:

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id`, `init.cwd`, `init.agent` (`"agy-review"`), `init.tools` (56 entries), `init.permission_mode` (`"request-review"`) |
| 2 | `step_update` | `step_index` 0, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 1, `step_type` `"agent_response"`, `state` `"ACTIVE"`, partial `text_delta` |
| 4 | `step_update` | `step_index` 1, `step_type` `"agent_response"`, `state` `"DONE"`, final `text_delta`, `duration_seconds`, `usage` |
| 5 | `step_update` | `step_index` 2, `step_type` `"checkpoint"`, `state` `"DONE"`, `duration_seconds`, `usage` |
| 6 | `result` | `conversation_id`, `status` `"SUCCESS"`, `response` (one JSON finding, `[P1] Use addition instead of subtraction in add function`), `duration_seconds`, `num_turns` 1, `usage` |

**Event counts by type**: 1 `init`, 4 `step_update`, 1 `result`.

`result.response` parses cleanly as one JSON object with all four
`agy-review` schema keys (`findings`, `overall_correctness`,
`overall_explanation`, `overall_confidence_score`) — same shape as the 1.1.8
capture, different exact wording (`"[P1] Use addition instead of
subtraction in add function"` vs. the 1.1.8 capture's `"[P1] Subtract
numbers instead of adding them in add function"`) — expected model
non-determinism on the same seeded bug, not a schema change.

`tests/test_result.py`'s `ResultForJobFixtureTest` reads this file's exact
bytes as `output_file` content end to end through the real harvest path
(`result.result_for_job`); its assertion text was updated to match this
capture's finding wording.
