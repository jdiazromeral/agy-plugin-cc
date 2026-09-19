#!/usr/bin/env python3
"""SessionEnd hook.

Historically this hook SIGTERM'd every `running` **job** tagged with the
ending **session** and immediately rewrote its stored `status` to
`"cancelled"`, without ever verifying the process actually died — the same
mistake `companion/cancel.py` was fixed to avoid (see AGENTS.md's "Never
report a cancellation you have not verified"). It also reversed the
documented promise that a `--background` job's **agent workspace** lives
under the **state dir** specifically so it can outlive the session that
launched it (see `companion/state.py`'s `resolve_job_workspace` docstring).

SessionEnd now does neither. It never signals a job's process, and it
never mutates a job record in `state.json` — not for the ending session,
and not for any other session's jobs either. A job whose process is still
alive when its session ends is not an error to clean up: it is exactly
what `--background` promised.

`/agy:status` (`companion/status.py`) derives that condition at READ time,
independently, as an **orphan** (see `.looper/knowledge/glossary.md`'s
`modernize-127` entry): a job whose session has ended while its process is
still alive, established by checking the recorded pid, never assumed from
the session's absence. `/agy:cancel` remains the only way to actually
terminate a job's process, orphaned or not, through its existing
verified-kill path (SIGTERM, poll for real exit, escalate to SIGKILL).

`cleanup_session_jobs` is therefore a deliberate no-op: it reads and writes
nothing. The mission's soft criteria considered recording "the session
ended" somewhere outside the job records, and rejected it — nothing
downstream needs to read that back, since `/agy:status` derives `orphan`
purely from a job's own `session_id` and its pid's live liveness, never
from anything this hook writes. Writing nothing also avoids re-introducing
the exact read-modify-write race over `state.json`'s `jobs` array that
`/agy:status`, `/agy:cancel`, and any live companion process also read and
write concurrently — part of what made the original defect dangerous.
Kept as a named function (rather than inlined into `main`) so "SessionEnd
does nothing to jobs" has one obvious place to test.
"""
import json
import sys


def cleanup_session_jobs(session_id):
    """Deliberately does nothing. See the module docstring."""
    return


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    session_id = payload.get("session_id")
    if session_id:
        cleanup_session_jobs(session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
