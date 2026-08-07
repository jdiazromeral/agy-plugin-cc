# Fixture provenance: delegate log captures

Three raw `--log-file` captures from a real, authenticated **agy 1.1.8**,
taken against a throwaway scratch git repo under the session scratchpad —
never `lab/agy-plugin-cc`, never committed anywhere else. They exist because
`tests/fake_agy.py`'s log lines had been written from memory, and
`CONVERSATION_NOT_FOUND_RE` was validated against that memory rather than
against the binary (see `tests/test_log_fidelity.py` for the two defects this
hid).

- **Date**: 2026-07-30
- **agy version**: 1.1.8
- **Conversation created and resumed**: `2735a5c6-e3f9-4551-a1f3-d746b8011a4f`
- **Scrubbing**: the only edit applied to any of these files is path
  redaction — `/Users/<user>` → `/Users/REDACTED`, the workspace and session
  path segments → `-REDACTED-WORKSPACE` / `REDACTED-SESSION`. Every log line
  relied on by a test is byte-for-byte as agy wrote it. No credentials appear
  in these logs (checked); the recurring `error getting token source: You are
  not logged into Antigravity` lines are agy's own startup noise, emitted
  even on a run that then logs `Auth succeeded` — see the note in AGENTS.md.

## `2026-07-30-fresh.log`

A fresh foreground delegate: `agy -p "Reply with exactly the word: OK"
--dangerously-skip-permissions --sandbox --new-project --log-file <path>`.
Exit 0, stdout `OK`.

Establishes both of these on 1.1.8:

- `server.go:1007] Created conversation 2735a5c6-…` — note the line number
  moved from `server.go:934` on 1.1.6 (M2's ledger). `CREATED_CONVERSATION_RE`
  is unaffected because it never anchored on the file or line number, which
  is exactly why it should stay that way.
- `conversation_manager.go:666] Stream completed for 2735a5c6-…, clearing
  ResponsePending` — the first live confirmation of the **completion marker**
  the whole `/agy:status` + `/agy:result` path rests on. Prior to this it was
  the plugin's largest unverified assumption.

## `2026-07-30-resume-genuine.log`

A genuine resume, driven through the shipped companion
(`agy_companion.py delegate --resume`), continuing the conversation above.
Confirms the trace shape `delegate.py`'s resume logic assumes:

- **no** `Created conversation` line — a real resume creates nothing, so the
  uuid can only come from the completion marker (`find_conversation`'s
  documented fallback);
- the completion marker carries the **same** uuid as the fresh run.

Semantically confirmed too: the run was asked what word it had just replied
and answered `ALPHA`, which only a genuinely resumed conversation could know.

## `2026-07-30-resume-fallback.log`

The **silent fallback** — `--conversation 00000000-0000-4000-8000-000000000000`
(a uuid that does not exist) with `--model bogus-model-xyz`.

Captured at **zero quota**: conversation resolution is logged before the local
`--model` validation exits, so this log contains the full fallback trace with
no `Created conversation` line and no `streamGenerateContent` call. That makes
the plugin's most dangerous guard re-verifiable for free on any future agy —
the same trick Finding A established for the agent **bind** check.

The binary emits **two** lines, and the difference between them is the whole
point of this fixture:

```
W… common.go:281] Conversation 00000000-… not found, ignoring --conversation flag
Warning: conversation "00000000-…" not found.
```

The klog trace leaves the uuid **bare**; the human-facing warning **quotes**
it. `CONVERSATION_NOT_FOUND_RE` originally required the quotes, so it matched
only the second line — leaving the guard resting on presentation text, the
likeliest thing to be reworded, localized, or suppressed under
`--output-format json`, while the durable trace slipped past.
