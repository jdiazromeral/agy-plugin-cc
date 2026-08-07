# Fixture provenance: agent_fallback capture

One raw **event stream** capture from a real, authenticated **agy 1.1.9**,
taken against a throwaway scratch git repo under a temp dir — never
`lab/agy-plugin-cc`, never any worktree, never committed anywhere else.
Captured by `tools/live_agent_fallback_capture.py` to witness **agent
fallback**: what a run does, end to end, when `--agent` names something
that does not exist.

This is the only capture in the repo of that behavior. The existing
`tests/fixtures/stream_events/` and `tests/fixtures/adversarial_review/`
captures are both `--new-project` runs that bound the agent they asked for
(`status: SUCCESS`, `init.agent` echoing a real agent name); the
`tests/fixtures/delegate/` logs cover the *other* **silent fallback** (an
unknown `--conversation` value, a **resume fallback**) but as klog TEXT, not
an **event stream**. Nothing before this fixture showed what the **event
stream** looks like when `--agent` itself doesn't resolve.

## carry-over-gap fix and re-capture (2026-08-02)

`tools/live_agent_fallback_capture.py` was forked from
`tools/live_stream_events_capture.py` before `--disable-slash-commands`
became unconditional on every command vector this plugin builds, and it did
not pick the flag up when the vectors did. It carried that gap forward
silently: the ORIGINAL `2026-08-01-run1.ndjson` fixture (below, now removed)
was captured by a command vector that never passed
`--disable-slash-commands`, a shape the real companion (`review._agy_command`
/ `delegate._agy_command`) would never actually build. The tool was fixed
(flag added, matching every other command vector) and the fixture replaced
with a fresh capture taken under the fixed tool, rather than leaving the
old, misleading-provenance capture in place.

The rule this stands for: a capture tool's argv must match the vector the
companion really builds, or the fixture proves nothing about the shipped
command.

- **Date**: 2026-08-02
- **agy version**: 1.1.9
- **Command**:
  ```
  agy -p "Say hello in five words." --disable-slash-commands \
      --agent definitely-nonexistent-agent-xyz \
      --sandbox --new-project --output-format stream-json --log-file <path>
  ```
  run with `cwd` set to a throwaway scratch git repo under a temp dir,
  `stdin` closed (`subprocess.DEVNULL`), `text=False` (raw stdout bytes
  written to disk immediately, before any parsing) — same discipline as
  `tools/live_stream_events_capture.py`.
- **Requested `--agent`**: `definitely-nonexistent-agent-xyz` — chosen to be
  obviously bogus, never a real agent name shipped by `agy` or this plugin.
- **Conversation UUID**: `d0ed0156-0de8-43de-ae2a-a3fbb7f9596a`
- **Exit code**: 0 — the **silent fallback** signature: no nonzero exit, no
  stderr line on stdout, the run just proceeds on a different agent.

### What moved between the old (un-fixed-tool) and new (fixed-tool) captures

Both captures are 1.1.9 (the original was already captured after the
upgrade — only the missing flag was wrong, not the agy version), so this is
a same-version A/B on the flag alone, not a version comparison:

- `init.tools` count: 56 in both. `init.permission_mode`: `"request-review"`
  in both. The flag's absence/presence made no observable difference to the
  **init event** shape.
- Event-kind sequence, step count (4 `step_update`s: `user_input` ->
  `unknown` -> `agent_response` -> `checkpoint`, all single-line `"DONE"`),
  and `result.status: "SUCCESS"` are identical in shape between the two
  captures — only the conversation UUID, exact token usage, and the
  assistant's exact reply text differ (expected run-to-run variance for a
  non-deterministic model call, not a structural difference).
- **Conclusion**: `--disable-slash-commands` has no visible effect on this
  particular prompt (`"Say hello in five words."` contains no leading `/`,
  so there was nothing for slash-command expansion to intercept either way)
  — its presence here is about the captured bytes matching the real
  command vector the companion actually builds, a provenance/honesty fix,
  not a behavior fix.

## The finding this capture exists to produce

The `--log-file` (never committed — see below) proves the fallback
happened, in cleartext:

```
W0802 ... printmode.go:178] Agent "definitely-nonexistent-agent-xyz" not found, falling back to default
```

But the **init event** — the only thing a caller sees on stdout, and the
one considered (and rejected) as a **structural bind proof** — does
**NOT** read back the resolved default. It reads back the exact bogus
string that was requested:

```json
"init":{"agent":"definitely-nonexistent-agent-xyz", ...}
```

**This is a load-bearing negative finding.** `init.agent` is not a report of
which agent bound — it is an echo of the `--agent` argv value, present
whether or not that value ever resolved to anything real. A **structural
bind proof** built by comparing the requested `--agent` string against
`init.agent` would read this run as "bound successfully to
`definitely-nonexistent-agent-xyz`" — exactly backwards. `init.agent`
cannot serve as a **bind proof** on its own; the **event stream** carries no
field that names the agent that actually ran. The only proof of what
happened here is the `--log-file` klog line above, which is exactly the
**log bind proof** the epic wants to move away from.

## Piece 5 the "Starting new conversation (agent=...)" line

This run's `--log-file` (not committed, quoted here for the record) reads:

```
I0802 ... conversation_manager.go:374] Starting new conversation (agent=false)
```

`agent=false` on a run whose `--agent` was **requested but did not
resolve** — the "argv-presence" hypothesis (the boolean just reflects
whether `--agent` was passed at all) predicts `true` here, since `--agent`
*was* passed. The observed `false` is evidence against that hypothesis; see
`tests/fixtures/stream_events/PROVENANCE.md`'s companion entry for the
positive (bound) side of this same comparison, and the mission's record
file for the combined conclusion. Note also the source line moved from
`:373` (the value quoted in earlier epic recon) to `:374` on this 1.1.9
build — do not anchor any pattern on this line number (AGENTS.md's standing
warning).

## Scrubbing

The only edit applied is path redaction, in the **init event**'s
`init.cwd` field and nowhere else — the scratch repo's absolute path (which
embedded this machine's username and the capturing session's uuid) was
rewritten:

`/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>/scratchpad/...`
-> `/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/scratchpad/...`,

matching the redaction discipline used in
`tests/fixtures/stream_events/PROVENANCE.md` and
`tests/fixtures/delegate/PROVENANCE.md` (`/Users/<user>` -> `/Users/REDACTED`,
workspace/session path segments -> placeholders). Every other byte — every
event, every field, `usage`, timing, the bogus agent name itself — is
exactly as `agy` wrote it. No credentials or emails appear in this fixture
(checked). The `--log-file` output quoted above (for the finding writeup
only) is **not** committed here — same discipline as
`tests/fixtures/stream_events/PROVENANCE.md`: only the NDJSON stdout is
committed; `--log-file`'s own auth noise is a separate file.

## `2026-08-02-run1.ndjson`

6 lines, one JSON object per line:

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id`, `init.cwd` (redacted), `init.agent` (`"definitely-nonexistent-agent-xyz"` — the REQUESTED name, not a resolved default), `init.tools` (56 entries), `init.permission_mode` (`"request-review"`) |
| 2 | `step_update` | `step_index` 0, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 1, `step_type` `"unknown"`, `state` `"DONE"`, `duration_seconds` |
| 4 | `step_update` | `step_index` 2, `step_type` `"agent_response"`, `state` `"DONE"`, full `text_delta` (`"Hello! How can I help?\n"`), `duration_seconds`, `usage` |
| 5 | `step_update` | `step_index` 3, `step_type` `"checkpoint"`, `state` `"DONE"`, `duration_seconds`, `usage` |
| 6 | `result` | `conversation_id`, `status` `"SUCCESS"`, `response` (`"Hello! How can I help?\n"`), `duration_seconds`, `num_turns` 1, `usage` |

**Event counts by type**: 1 `init`, 4 `step_update`, 1 `result`.

`init.init`'s key set is exactly `{"agent", "cwd", "permission_mode",
"tools"}`, matching the original (now-removed) capture and the two
`_CAPTURES` fixtures.

Every **step update** here shows `state` `"DONE"` only — no `"ACTIVE"`
appears, same shape as the original capture: each step arrived as one
complete line rather than a `"DONE"` line preceded by partial `"ACTIVE"`
deltas. `step_type` `"unknown"` (step_index 1, no `text_delta`, no tool
fields) reappears here too — recorded again as observed vocabulary, not
explained further.

`result.response` is plain text (`"Hello! How can I help?\n"`), not a JSON
review-schema object — expected, since the prompt was a trivial "say hello"
request. This fixture is not review-shaped and does not fit
`tests/test_log_fidelity.py`'s `_CAPTURES` tuple (which asserts
review-schema `response` JSON); it is asserted by its own dedicated test
class instead.
