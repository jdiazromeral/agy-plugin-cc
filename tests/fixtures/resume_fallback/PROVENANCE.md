# Fixture provenance: resume_fallback capture

The first committed **event stream** (`.ndjson`) witnessing a **resume
fallback** — `agy`'s **silent fallback** behavior for `--conversation`,
distinct from **agent fallback** (glossary: "they have different traces and
different structural detectors, and calling both 'the fallback' has already
cost one debugging session"). Every prior committed evidence of this
specific fallback
(`tests/fixtures/delegate/2026-07-30-resume-fallback.log`) is a klog TEXT
`--log-file` capture, not an **event stream** — this fixture closes that
gap.

## The free probe was tried first, and it does NOT answer this gap

Per the mission's explicit instruction, the zero-quota bind-probe trick
(`--model bogus-model-xyz`, which exits on local model validation before
any model call) was tried FIRST, paired with a bogus `--conversation`, to
see whether it yields a usable **event stream** for the **resume fallback**
gap. **It does not, and this is reported honestly rather than assumed either
way**: the paired-bogus-model run's stdout was exactly ONE line — a
**result event** with `status: "ERROR"`, `conversation_id: ""` (empty), and
`error` naming the *model* validation failure
(`"invalid model selection (--model \"bogus-model-xyz\"...)"`), with NO
**init event** at all. The `--log-file` for that same free run DID show the
conversation silently falling back (`common.go:283] Conversation ...
not found, ignoring --conversation flag`) — but that fact never reaches
stdout. Local model validation fails and exits BEFORE the run ever gets far
enough to emit an **init event** reflecting the (already-resolved) fallback
conversation. The free probe is therefore free but useless for this
specific gap: it proves the log-level fallback trace still fires, which was
already known, but produces no **event stream** bytes a caller would ever
see for a **resume fallback** specifically — the observed ERROR shape is an
artifact of the paired bogus model, not of the **resume fallback** itself. This
free attempt was not committed as a fixture (it does not witness the thing
this fixture needs to witness); it is recorded here, and in the mission's
record file, so the negative is not silently lost.

## The real (paid) mechanism does work — this fixture is the positive case

A second, real (quota-spending) run — `--conversation <bogus-uuid>
--output-format stream-json`, a REAL default model, no `--model` override —
completes normally end to end, because the **silent fallback** is genuinely
silent: agy proceeds on a freshly created conversation with no interruption
at all. This is a full, valid **event stream**: `init` -> `step_update`(x2)
-> `result`, `status: "SUCCESS"`.

- **Date**: 2026-08-02
- **agy version**: 1.1.9
- **Command** (mirrors `delegate._resume_agy_command`'s real vector, given a
  `--conversation` value that cannot resolve):
  ```
  agy -p "Reply with exactly the word: OK" \
      --disable-slash-commands --dangerously-skip-permissions --sandbox \
      --conversation 00000000-0000-4000-8000-000000000000 \
      --output-format stream-json --log-file <path>
  ```
  run with `cwd` set to a fresh, throwaway scratch git repo under a temp
  dir (never `lab/agy-plugin-cc`, never any worktree), `stdin` closed
  (`subprocess.DEVNULL`), `cwd=` passed explicitly to `subprocess.run`.
- **Requested `--conversation`**: `00000000-0000-4000-8000-000000000000` —
  the same bogus-but-syntactically-valid uuid every other fallback probe in
  this repo uses (`tools/live_delegate_capture.py`,
  `tests/fixtures/delegate/2026-07-30-resume-fallback.log`).
- **Actual (fallen-back-to) conversation**: `dbf4949e-4897-4735-ba30-efa865b105a2`
  — a BRAND NEW conversation, confirmed both by the **init event**'s
  `conversation_id` (not the bogus requested value) and by the `--log-file`
  (not committed) carrying `common.go:283] Conversation
  00000000-0000-4000-8000-000000000000 not found, ignoring --conversation
  flag` immediately followed by
  `conversation_manager.go:374] Starting new conversation (agent=false)`
  and `server.go:1017] Created conversation dbf4949e-...`.
- **Exit code**: 0 — the **silent fallback** signature: no nonzero exit, no
  stderr, the run just proceeds on a new conversation.

### The load-bearing structural finding this fixture pins

Unlike **agent fallback** (`tests/fixtures/agent_fallback/`, where
`init.agent` echoes the REQUESTED bogus value and cannot be used to detect
the fallback), a **resume fallback**'s **event stream** DOES carry a
structural tell: `init.conversation_id` (and `result.conversation_id`) read
the ACTUAL new conversation, never the bogus requested one. Comparing the
requested `--conversation` value against the stream's `conversation_id` —
exactly what `delegate._resume_bind_check` already does — correctly
detects this fallback with no `--log-file` needed.
`tests/test_log_fidelity.py`'s `test_resume_bind_check_rejects_a_different_conversation`
already pins this behavior against the review-shaped `_CAPTURES`; this
fixture is the first REAL **resume fallback** capture to confirm the same
detector against the fallback shape itself, not just a bogus uuid asserted
in a test.

### Piece 5 the "Starting new conversation (agent=...)" line

This run passed no `--agent` at all (the delegate command vector never
does), so it does not directly answer piece 5's bound-vs-unresolved
question — see `tests/fixtures/agent_fallback/PROVENANCE.md` and
`tests/fixtures/stream_events/PROVENANCE.md` for the two runs that do. It
is recorded here for completeness: `agent=false`, consistent with every
other no-`--agent`-flag capture in this repo
(`tests/fixtures/delegate/2026-07-30-fresh.log`,
`tests/fixtures/agent_fallback/`'s bogus-agent run).

### Scrubbing

The only edit applied is path redaction, in the **init event**'s
`init.cwd` field — the scratch repo's absolute path (embedding this
machine's username and the capturing session's uuid) was rewritten:
`/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>/` ->
`/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/`, matching
every other fixture's redaction discipline. Every other byte is exactly as
`agy` wrote it. No credentials or emails appear in this fixture (checked).

## `2026-08-02-run1.ndjson`

6 lines, one JSON object per line:

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id` (the NEW fallback conversation, not the bogus requested one), `init.cwd` (redacted), `init.tools`, `init.permission_mode` (`"always-proceed"`) — no `init.agent` key, same as `tests/fixtures/resume/` |
| 2 | `step_update` | `step_index` 0, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 1, `step_type` `"unknown"`, `state` `"DONE"`, `duration_seconds` |
| 4 | `step_update` | `step_index` 2, `step_type` `"agent_response"`, `state` `"DONE"`, full `text_delta` (`"OK\n"`), `duration_seconds`, `usage` |
| 5 | `step_update` | `step_index` 3, `step_type` `"checkpoint"`, `state` `"DONE"`, `duration_seconds`, `usage` |
| 6 | `result` | `conversation_id` (the new fallback conversation — differs from the requested bogus value, the fallback tell), `status` `"SUCCESS"`, `response` (`"OK\n"`), `duration_seconds`, `num_turns` 1, `usage` |

**Event counts by type**: 1 `init`, 4 `step_update`, 1 `result`.
