# Fixture provenance: command_result capture

One raw **event stream** capture from a real, authenticated **agy**, taken
via a read-only slash-command probe — free per `AGENTS.md`'s settled
findings ("Read-only slash commands are answered for free in print mode"),
so this capture spent none of the mission's live `agy` quota (distinct from
`tests/fixtures/denied_actions/`, which spent the mission's one paid run).

Staged uncommitted at
`.looper/epics/modernize-127/M5_command_result_capture.ndjson` in the
primary repository by the mission orchestrator during preflight research,
per `.looper/epics/modernize-127/records/M5_001_record_file.md` and the
contract's own "Provenance and the M4 interaction" section
(`M5_purpose.md`). No dedicated `tools/live_*_capture.py` script exists for
this vector.

This capture is a fresh, independent confirmation of a shape `AGENTS.md`
already documents by name — its "stream-json gained a `command_result`
event in 1.1.11" settled finding (verified there against agy 1.1.11,
2026-08-07) shows the identical `{"event":"command_result","command":
{"name":"model","data":{...}}}` shape and an all but identical `result`
envelope for the same `/model` probe. What this capture adds: it is taken
against the currently installed 1.2.7 binary (not 1.1.11), and it is the
one committed as an actual fixture file rather than only quoted in prose.

## `2026-09-20-run1.ndjson`

- **Date**: 2026-09-20 (per the mission record file).
- **agy version**: 1.2.7 — the version installed for this mission (see
  `tests/fixtures/denied_actions/PROVENANCE.md` for the same binary's
  version confirmation).
- **Command vector**: `agy -p "/model" --output-format stream-json
  --print-timeout <duration> --log-file <path>`, `stdin` closed. Per
  `AGENTS.md`'s "`--disable-slash-commands` defeats the free-probe class,
  expensively" finding, a read-only slash-command probe deliberately
  OMITS `--disable-slash-commands` — the flag exists to stop *user-supplied
  text* from being slash-expanded, and a probe sends a plugin-authored
  constant, never user text, so omitting it here is the one correct
  exception to this plugin's otherwise-unconditional rule. No `--agent`
  flag: a read-only slash command answers before any agent or model turn
  starts (`num_turns: 0` below), so there is nothing to bind.
- **Bound agent**: none — not applicable. A read-only slash-command probe
  answers without starting an agent turn at all (see `AGENTS.md`); this is
  not a review, adversarial-review, or delegate run and carries no **bind
  proof** to speak of.
- **Conversation UUID**: `""` (the empty string) — `result.conversation_id`
  is genuinely empty for this vector, matching `AGENTS.md`'s documented
  sample byte for byte. Not a scrubbed or redacted value; `agy` writes an
  empty string here because no conversation was created.
- **Exit code**: 0.

### Lines

| # | `event` | notes |
|---|---|---|
| 1 | `command_result` | `command.name` (`"model"`), `command.data` (`id` `"gemini-3.8-flash-medium"`, `label` `"Gemini 3.8 Flash (Medium)"`, `effort` `"medium"`, `is_default` `false`) |
| 2 | `result` | `conversation_id` (`""`), `status` (`"SUCCESS"`), `response` (`"gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"` — plain-text tab-separated, per `AGENTS.md`), `duration_seconds` (`0`), `num_turns` (`0`), `usage` (all-zero), **and its own `command` key carrying the identical payload as line 1's `command_result` event** |

**Event counts by type**: 1 `command_result`, 0 `init`, 0 `step_update`,
1 `result`. This is the first committed capture with no **init event** at
all — a read-only slash-command probe never starts a conversation, so
there is nothing for an init event to announce. `stream_events.py`'s
parser must not assume an init event always arrives first; this fixture is
exactly the case that pins that.

**Why the `result` event's own `command` key matters**: it means a caller
reading a truncated stream that stopped before the dedicated
`command_result` line — or one that, for whatever reason, never emitted
one — can still recover the command payload from the terminal `result`
event alone. Both captured here, byte-identical, is the real-bytes proof
that both paths exist and agree; `companion.stream_events.parse_event_stream`
reads either.

## Scrubbing

None needed or applied — this capture carries no filesystem path, no
credential, and no email address of any kind (checked). Both bytes are
committed exactly as `agy` wrote them.
