# Fixture provenance: error_result capture

The first real captured **ERROR-status result event** this project has ever
had. `tests/test_result.py`'s `ResultForJobCrashedResultEventTest` said
plainly, in its own docstring, that its coverage was synthetic NDJSON
"built from `stream_events.py`'s own field names" and that "no real
captured ERROR-status bytes exist yet" — the one real historical instance
(job `review-833305131436`, "timeout waiting for response") lived in a temp
state dir and is gone. a later capture provoked a fresh
one deliberately, per piece 3's third capture gap and piece 4's honesty
debt.

- **Date**: 2026-08-02
- **agy version**: 1.1.9
- **Command**:
  ```
  agy -p "Write an exhaustive, deeply reasoned 3000-word essay analyzing \
      the philosophical, computational, and ethical implications of the \
      halting problem for artificial general intelligence. Cover at least \
      twelve distinct arguments in extensive technical detail, with worked \
      examples for each, and a rigorous rebuttal section for every \
      argument." \
      --disable-slash-commands --dangerously-skip-permissions --sandbox \
      --new-project --output-format stream-json --print-timeout 5s \
      --log-file <path>
  ```
  run with `cwd` set to a fresh, throwaway scratch git repo under a temp
  dir, `stdin` closed (`subprocess.DEVNULL`), `cwd=` passed explicitly to
  `subprocess.run`.
- **Mechanism, confirmed empirically, not assumed**: `--print-timeout 5s`
  bounds how long `agy` waits for a model response in print mode (default
  `5m0s`, confirmed free via `agy --help` before this mission spent any
  quota). Paired with a prompt engineered to keep the model generating past
  5 seconds (a demand for an exhaustive, deeply-reasoned long-form essay),
  the first attempt produced an ERROR **result event** — no retry needed.
  The `--log-file` (not committed) confirms the timing precisely:
  `printmode.go:244] Print mode: conversation=..., sending message` at
  `13:12:23.424`, `printmode.go:496] Print mode: timed out after 25 polls
  (printed=2)` at `13:12:28.445` — almost exactly 5.02s later, matching
  `--print-timeout 5s`.
- **Conversation UUID**: `6bfaf836-0988-4b34-ac6a-0073c7747bde`
- **Exit code**: 1 — unlike every **silent fallback**, an ERROR **result
  event** DOES surface as a nonzero process exit, in addition to the
  stream's own `status` field.

## The real ERROR result event's exact shape

```json
{
  "conversation_id": "6bfaf836-0988-4b34-ac6a-0073c7747bde",
  "status": "ERROR",
  "response": "",
  "error": "timeout waiting for response",
  "duration_seconds": 0.025711,
  "num_turns": 1,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "thinking_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0
  }
}
```

**Key set**: `{"conversation_id", "status", "response", "error",
"duration_seconds", "num_turns", "usage"}` — the same seven keys a
`SUCCESS` **result event** carries, plus `error`. `response` is present but
the empty string (not absent) — consistent with `result.py`'s
`_render_result`, which checks `status` before ever looking at `.response`
for the harvest path, so this does not matter to the shipped renderer, but
it does matter to `status.derive_status` and any future code that might
naively branch on `.response` truthiness instead of `.status`.

**`error`**: `"timeout waiting for response"` — this is the EXACT string
the epic's one prior real (now-gone) instance carried (job
`review-833305131436`). Confirmed twice now, by two independent instances,
that this is agy's real wording for a print-mode timeout, not a guess.

## A finding that corrects the glossary — reported here, not edited there

`.looper/knowledge/glossary.md`'s **result event** entry currently reads:
"A killed or timed-out run emits a result event with `status: "ERROR"`, an
empty `response`, a human-readable `error`, and **a fully populated usage
block**." That last clause is **wrong**, measured against this real
capture: the `usage` block above is not "fully populated" — every single
token count in it is `0`. This glossary text was written before any real
ERROR bytes existed (an inference about what a "killed mid-stream" run's
partial usage might look like), and this capture — a `--print-timeout`
expiry before the model ever started streaming a response (`0` step_update
`agent_response` events; the stream shows only `user_input` and one
`unknown` step before jumping straight to the ERROR result) — shows the
opposite: no tokens had been counted at all when the timeout fired. This
does not necessarily mean EVERY ERROR **result event** carries an
all-zero `usage` (a run killed further into generation, with partial output
already streamed, might carry partial non-zero counts — this capture does
not test that case, and no fixture claims otherwise). What is measured,
precisely: THIS ERROR shape — a `--print-timeout` expiry with no prior
`agent_response` step — carries an all-zero `usage`, not a "fully
populated" one. Per this mission's Method section, the worker does not
edit `.looper/knowledge/glossary.md` directly; this finding is also
recorded in the mission's record file for the orchestrator to correct.

## No code fix required — verified, not assumed

`result._render_result`'s ERROR branch reads only `event_stream.status` and
`event_stream.error`, in that order, before ever reaching the
`.response`/`usage` harvest path:

```python
if event_stream is not None and event_stream.status is not None and event_stream.status != "SUCCESS":
    return "Job {} did not finish successfully (status: {}): {}".format(
        job.get("id"), event_stream.status,
        event_stream.error or "(no error message captured)",
    )
```

Run over this real fixture's bytes (via
`tests/test_log_fidelity.py`'s `RealErrorResultEventTest`, mirroring
`ResultForJobCrashedResultEventTest`'s synthetic assertions but against
these real bytes), it renders `"Job <id> did not finish successfully
(status: ERROR): timeout waiting for response"` — `status` and `error`
rendered, never the raw NDJSON dump. No code change was needed in
`result.py` or `stream_events.py`: the shipped path already handles the
real shape correctly. `test_result.py`'s `ResultForJobCrashedResultEventTest`
docstring is updated to stop claiming "no real captured
ERROR-status bytes exist yet" — that claim is now false — while keeping its
synthetic cases (they still cover branch combinations, e.g. non-review
`kind`, a `FAILED` non-`"ERROR"` status, a missing `error` string, that this
one real capture does not exercise on its own).

## Scrubbing

The only edit applied is path redaction, in the **init event**'s
`init.cwd` field — the scratch repo's absolute path (embedding this
machine's username and the capturing session's uuid) was rewritten:
`/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>/` ->
`/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/`, matching
every other fixture's redaction discipline. Every other byte is exactly as
`agy` wrote it. No credentials or emails appear in this fixture (checked).

## `2026-08-02-run1.ndjson`

4 lines, one JSON object per line:

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id`, `init.cwd` (redacted), `init.tools`, `init.permission_mode` (`"always-proceed"`) — no `init.agent` key (no `--agent` passed) |
| 2 | `step_update` | `step_index` 0, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 1, `step_type` `"unknown"`, `state` `"DONE"`, `duration_seconds` — no `agent_response` step ever started; the timeout fired before generation began streaming |
| 4 | `result` | `status` `"ERROR"`, `response` `""`, `error` `"timeout waiting for response"`, `duration_seconds` 0.025711, `num_turns` 1, `usage` (all-zero) |

**Event counts by type**: 1 `init`, 2 `step_update`, 1 `result`. Notably
SHORTER than every SUCCESS capture in this repo (no `agent_response` or
`checkpoint` step at all) — the timeout preempted the run before either
occurred.
