# Fixture provenance: resume capture

The first committed **event stream** (`.ndjson`) witnessing a genuine
**resume** (`--conversation <valid-uuid>` with `--output-format
stream-json`). Every prior committed resume evidence
(`tests/fixtures/delegate/2026-07-30-resume-genuine.log`) is a klog TEXT
`--log-file` capture, not an **event stream** — this fixture closes that
gap.

Captured with an ad-hoc script (not a new `tools/` file — this mission's
scope is the capture bytes and their pins, not a new reusable tool), which
follows the exact discipline every `tools/live_*_capture.py` script uses:
throwaway scratch git repo under a temp dir (reused from
`tests/fixtures/stream_events/`'s own capture — see below), `stdin` closed
(`subprocess.DEVNULL`), `text=False` raw bytes written to disk before any
parsing, real `cwd=` passed explicitly to `subprocess.run` (never inferred
from a shell's ambient working directory).

- **Date**: 2026-08-02
- **agy version**: 1.1.9
- **Command** (mirrors `delegate._resume_agy_command`'s real vector
  byte-for-byte — `--dangerously-skip-permissions --sandbox`, `--conversation`
  in place of `--new-project`, no `--agent`):
  ```
  agy -p "In one sentence, what file did you find the bug in during your \
      last review, and what was the bug?" \
      --disable-slash-commands --dangerously-skip-permissions --sandbox \
      --conversation a4425612-2b6c-4e0c-a9b5-e7600418be81 \
      --output-format stream-json --log-file <path>
  ```
  run with `cwd` set to the SAME scratch repo
  `tests/fixtures/stream_events/PROVENANCE.md`'s 2026-08-02 capture created
  (`--conversation` resumes by uuid, not by cwd, but the repo — including
  the seeded `calc.py` bug — is exactly what the resumed conversation's
  history refers back to).
- **Resumed conversation**: `a4425612-2b6c-4e0c-a9b5-e7600418be81` — the
  SAME conversation `tests/fixtures/stream_events/2026-08-02-run1.ndjson`
  created. One live run produced the source conversation (piece 2's
  re-capture), a second resumed it (this fixture) — no separate live run
  spent creating a throwaway conversation solely to resume it, per the
  mission's Method section.
- **Exit code**: 0

### An honest complication: this conversation was resumed twice, not once

The capturing worker made a real operational mistake during this capture:
a first resume attempt was launched from a bash command that relied on the
shell's ambient working directory rather than an explicit `cwd=` — the
shell's cwd had drifted to `lab/agy-plugin-cc` (a SIBLING repo, not this
worktree) between commands, so that attempt's **init event** showed the
wrong `init.cwd` and is NOT the fixture committed here. `git status` in
`lab/agy-plugin-cc` was checked immediately afterward and confirmed clean —
the run was read-only (one `find_by_name` call and a text response, no
write tool calls) — but it was a real, quota-spending `agy -p` call
against the SAME conversation uuid, discarded rather than committed. See
the mission's record file for the full account. Every subsequent live
invocation in this mission went through a small script
(`run_agy.py`, not committed — a session-scratchpad helper, not a
`tools/` deliverable) that passes `cwd=` explicitly to `subprocess.run`,
never relying on shell state, to make this class of mistake structurally
impossible going forward.

**Consequence for this fixture's bytes**: the conversation's `num_turns`
reads `3`, not the `2` a single fresh-then-resume pair would show — turn 1
was the original review (piece 2's capture), turn 2 was the discarded
wrong-cwd attempt, turn 3 is the genuine resume committed here. The
**step_update** `step_index` values also start at `8`/`9`/`10`, not `3`,
for the same reason (the per-conversation step counter is not reset by a
resume and was already advanced by the discarded attempt). None of this
was edited or renumbered — it is committed exactly as `agy` produced it,
because it is real evidence of what a real resume's counters look like when
a conversation has more history than one turn, not a defect to hide. It
also incidentally strengthens the resume proof: a resume genuinely
continuing a conversation's step-index sequence across processes is
exactly the trace shape `_resume_bind_check` and `find_conversation` depend
on.

### Semantic proof of a genuine resume

Asked (with no diff or context re-supplied) what file it found a bug in
during "your last review" and what the bug was, the run answered
correctly and specifically — `calc.py`, the `add` function, returning the
difference instead of the sum — which only a genuinely resumed conversation
with the original review's context could know. It even used `find_by_name`
to relocate `calc.py` by its real (redacted) absolute path before
answering, rather than guessing.

### Structural shape: `init` carries no `agent` key at all

Unlike every `--agent`-passing capture (`tests/fixtures/stream_events/`,
`tests/fixtures/adversarial_review/`, `tests/fixtures/agent_fallback/`, all
four keys `{"agent", "cwd", "permission_mode", "tools"}`), this **init
event**'s key set is exactly `{"cwd", "permission_mode", "tools"}` — no
`"agent"` key at all, present or null. `delegate._agy_command` never passes
`--agent`, and the **event stream** reflects that by omitting the key
entirely rather than emitting `"agent": null`. Any code or test that
assumes the four-key set unconditionally would be wrong for this — and
every — delegate-shaped capture.

### Scrubbing

The only edit applied is path redaction — the scratch repo's absolute path
(embedding this machine's username and the capturing session's uuid)
appears in `init.cwd`, twice in `step_update.text_delta` (a
`file://` markdown link, split mid-URL across two streamed chunks), and
once more in the final `result.response`. Every occurrence was rewritten
identically: `/private/tmp/claude-501/-Users-<user>-workspace-.../<session-uuid>/`
-> `/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/`, matching
every other fixture's redaction discipline. Every other byte — every
event, every field, `usage`, timing, `num_turns`, `step_index` — is exactly
as `agy` wrote it. No credentials or emails appear in this fixture
(checked).

## `2026-08-02-run1.ndjson`

6 lines, one JSON object per line:

| # | `event` | notes |
|---|---|---|
| 1 | `init` | `conversation_id`, `init.cwd` (redacted), `init.tools`, `init.permission_mode` (`"always-proceed"`) — no `init.agent` key |
| 2 | `step_update` | `step_index` 8, `step_type` `"user_input"`, `state` `"DONE"` |
| 3 | `step_update` | `step_index` 9, `step_type` `"system_message"`, `state` `"DONE"` — a step type not seen in any prior fixture |
| 4 | `step_update` | `step_index` 10, `step_type` `"agent_response"`, `state` `"ACTIVE"`, partial `text_delta` |
| 5 | `step_update` | `step_index` 10, `step_type` `"agent_response"`, `state` `"DONE"`, final `text_delta`, `duration_seconds`, `usage` |
| 6 | `result` | `conversation_id` (same as requested — a genuine resume, not a fallback), `status` `"SUCCESS"`, `response`, `duration_seconds`, `num_turns` 3, `usage` |

**Event counts by type**: 1 `init`, 4 `step_update`, 1 `result`.

`result.conversation_id` equals the REQUESTED `--conversation` value
exactly — the structural signal `delegate._resume_bind_check` already
relies on (`tests/test_log_fidelity.py`'s
`test_resume_bind_check_binds_the_real_conversation` pins this same
equality for the review-shaped captures; this fixture is the first to pin
it for a genuine DELEGATE-shaped resume specifically).
