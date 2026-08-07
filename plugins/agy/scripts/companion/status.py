"""companion.status — the `status` subcommand: reads the current repo's
**state dir** and renders a compact table of active/recent **job**s.

With `agy` spawned detached and no babysitter process, a **job**'s exit code
is never captured by any later Claude Code turn. Status is derived from two
independent sources, each re-read fresh on every call:

  - the raw `--log-file` text, checked ONLY for the **silent fallback**
    trace line, which always wins over everything else (per AGENTS.md's
    "Never trust an agy run that silently fell back") — a job that fell
    back never produced a real result, no matter what its **event stream**
    later shows. The stream carries no agent field, so this one check must
    keep reading the log.
  - otherwise, the job's `output_file`, parsed as an **event stream**
    (`companion.stream_events.parse_event_stream`): a **result event**
    with `status == "SUCCESS"` means completed; a **result event** present
    with any other `status` means bound but crashed (no specific failure
    string is special-cased — no real crash value has ever been observed
    live); no **result event** at all — absent, partial, or unparseable
    `output_file` content, or a job simply still running — means running,
    deliberately. That last case is ambiguous on purpose: "crashed with no
    trace", "killed", and "genuinely still running" all look identical
    here, and the **stall** detection is what tells them apart, built on
    top of this same signal.

`build_job_row`'s `stall` field is that detection: a `status == running`
job with no sign of life (`output_file`'s own mtime, the same proxy
`elapsed` already applies to `log_file`) for longer than
`_STALL_THRESHOLD_SECONDS` is reported stalled — a decoration layered on
top of `status`, never a new status of its own, since there is still no
exit code to consult and a "stalled" job may yet resume. `usage` surfaces
the **result event**'s token counts once one has landed; both are `None`
until their respective condition holds, never a fabricated zero or false.

Reads only; never writes state, never touches `agy` or spawns anything.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from companion import repo as repo_arg
from companion import state
from companion.agy_log import FALLBACK_RE, find_conversation
from companion.git import GitTargetError, ensure_git_repository
from companion.stream_events import parse_event_stream

HELP = "Show active and recent agy jobs for this repo, derived from their persistent logs."

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUS_SILENT_FALLBACK = "error: silent fallback"
STATUS_CRASHED = "error: bound but crashed"
# A stored-only terminal status /agy:cancel writes (see companion.cancel).
# The log format has no way to express "cancelled" — there is no babysitter
# process to write anything to it after the signal is sent — so a stored
# `cancelled` status must be trusted rather than re-derived. Generalized by
# `_is_stored_terminal_status` below to every **terminal status** the
# glossary names, not just this one.
STATUS_CANCELLED = "cancelled"

# The glossary-anchored **terminal status** set — completed,
# cancelled, or the bare `error` a foreground delegate stores on a nonzero
# exit (delegate.py). `error:` VARIANTS (STATUS_SILENT_FALLBACK,
# STATUS_CRASHED, and any future one) are matched by prefix in
# `_is_stored_terminal_status`, not listed here, since new variants must not
# require touching this set. Deliberately NOT "anything that is not
# running" — a stored `queued` (a status this codebase never itself writes,
# but must not be trusted as terminal either) still falls through to
# derivation; see test_missing_log_file_and_no_output_file_reads_as_running_not_a_crash
# in tests/test_status.py.
_STORED_TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_ERROR)


def _is_stored_terminal_status(stored_status):
    """True if `stored_status` (a job record's raw `status` field, BEFORE
    any re-derivation) is one of the glossary's **terminal status** values:
    `completed`, `cancelled`, or an `error`/`error: ...` variant. Anything
    else — `running`, `queued`, or any other value — is not trusted and
    must be re-derived by `derive_status`. See `build_job_row`."""
    if stored_status in _STORED_TERMINAL_STATUSES:
        return True
    return isinstance(stored_status, str) and stored_status.startswith("error:")


# The same session-scoping env var review.py tags a background
# job with (see companion.state.SESSION_ID_ENV's docstring for why it lives
# there). When absent (or the user passes --all-sessions), status shows
# every job for the repo rather than crashing or silently showing nothing.
SESSION_ID_ENV = state.SESSION_ID_ENV

_LOG_TAIL_MAX_CHARS = 80

# A starting-point ceiling, not a measured one — no telemetry exists yet on
# real **step update** cadence. 10 minutes is chosen to be well past any
# single review/delegate turn seen in this repo's fixtures (the real captured
# run in tests/fixtures/stream_events/ finishes in ~3s) while staying short
# enough to be useful; move it if live jobs
# prove it too tight (flags healthy-but-slow runs) or too loose (leaves a
# genuinely stuck job unreported for too long).
_STALL_THRESHOLD_SECONDS = 10 * 60


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON array instead of the table.",
    )
    parser.add_argument(
        "--all-sessions",
        action="store_true",
        help="Show jobs from every Claude session, not just the current one.",
    )


def run(args):
    try:
        repo_root = ensure_git_repository(repo_arg.resolve_repo(args))
    except GitTargetError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    jobs = state.list_jobs(repo_root)
    session_id = None if args.all_sessions else os.environ.get(SESSION_ID_ENV)
    scoped = _scope_to_session(jobs, session_id)
    rows = [build_job_row(job) for job in scoped]

    if args.json:
        print(json.dumps(rows))
    else:
        print(render_status_table(rows, session_scoped=bool(session_id)))
    return 0


def _scope_to_session(jobs, session_id):
    if not session_id:
        return jobs
    return [job for job in jobs if job.get("session_id") == session_id]


# --- log/event-stream -> status derivation -----------------------------------


def _parse_event_stream(text):
    """Parse `text` (a job's `output_file` bytes) as an **event stream**,
    degrading to `None` on any parse failure rather than raising. Empty
    output (nothing captured yet), a partial capture (a still-running or
    killed job), and legacy/malformed content are all treated the same way:
    "no result event yet" — the identical signal a stream that simply
    hasn't reached one produces. Imported into companion.result for the
    harvest fallback (mirrors delegate.py/review.py importing
    companion.launch._spawn_detached across companion modules)."""
    if not text:
        return None
    try:
        return parse_event_stream(text)
    except Exception:
        # Parse_event_stream raises json.JSONDecodeError on
        # malformed NDJSON, and can raise AttributeError/TypeError when a
        # line decodes to a valid-but-non-object JSON value (e.g. a bare
        # string or array, so `.get` fails). Every one of those is "not a
        # usable event stream yet", never a /agy:status crash — see the
        # acceptance criteria's "never raises on empty, partial, or
        # unparseable content."
        return None


def derive_status(log_text, event_stream=None):
    """Derive a **job**'s live status from the raw `--log-file` text (used
    ONLY for the **silent fallback** check) plus its parsed **event stream**
    (or `None` if no **result event** has landed yet). Order matters — see
    the module docstring:

      1. `FALLBACK_RE` match on the log always wins — a job that silently
         fell back never produced a real result, no matter what the event
         stream shows.
      2. `event_stream.status == "SUCCESS"` -> completed.
      3. `event_stream.status` present and not `"SUCCESS"` -> crashed,
         treated generically ("present and not SUCCESS") — no specific
         failure string is special-cased.
      4. Otherwise (`event_stream` is `None`, or its `.status` is `None`)
         -> running, explicitly. This deliberately conflates "crashed with
         no trace", "killed", and "still running" — there is no exit code
         to distinguish them here. Never default this case to completed or
         crashed; the **stall** detection builds on top of this same
         ambiguity.
    """
    if FALLBACK_RE.search(log_text):
        return STATUS_SILENT_FALLBACK
    result_status = event_stream.status if event_stream is not None else None
    if result_status == "SUCCESS":
        return STATUS_COMPLETED
    if result_status is not None:
        return STATUS_CRASHED
    return STATUS_RUNNING


def build_job_row(job, now=None):
    """Build one **status** table row for `job` (a state.py job record),
    re-reading its `--log-file` and `output_file` fresh every call — except
    that a stored **terminal status** (`_is_stored_terminal_status`:
    `completed`, `cancelled`, or an `error`/`error: ...` variant) is
    authoritative and short-circuits derivation entirely. No code path stores a
    terminal status without having established it (delegate.py's fresh and
    resume-bound launches only store `completed`/`error` after the process
    has actually exited; cancel.py only stores `cancelled` after confirming
    the process is gone), so trusting it here does not weaken AGENTS.md's
    "never trust an agy run that silently fell back" guard — that guard is
    about NOT storing a terminal status on a silent fallback in the first
    place, which delegate.py already does not do. A stored `running` (or
    any other non-terminal value, e.g. `queued`) is never trusted this way
    — it is always re-derived. Never raises on empty, partial, or
    unparseable `output_file` content — see `_parse_event_stream`.

    Two additional keys are decorations on top of `status`, never a status
    transition of their own (see `_compute_stall`'s docstring and
    STATUS_RUNNING's meaning, unchanged by either): `stall` (a formatted
    duration, or `None` when not running or not stalled) and `usage` (the
    **result event**'s raw token-count dict, or `None` before one has
    landed — never synthesized zeros)."""
    log_file = job.get("log_file")
    output_file = job.get("output_file")
    log_text = state.read_file_safe(log_file)
    event_stream = _parse_event_stream(state.read_file_safe(output_file))
    stored_status = job.get("status")
    if _is_stored_terminal_status(stored_status):
        status_value = stored_status
    else:
        status_value = derive_status(log_text, event_stream)
    conversation = find_conversation(log_text) or job.get("conversation")
    elapsed = _format_elapsed(job, status_value, log_file, now=now)
    usage = event_stream.usage if event_stream is not None else None
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "status": status_value,
        "conversation": conversation,
        "elapsed": elapsed,
        "stall": _compute_stall(job, status_value, output_file, now=now),
        "usage": usage,
        "log_tail": _log_tail(log_text),
    }


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _log_end_time(log_file):
    if not log_file:
        return None
    try:
        mtime = os.path.getmtime(log_file)
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def _format_elapsed(job, status_value, log_file, now=None):
    start = _parse_iso(job.get("created_at"))
    if start is None:
        return None
    if status_value == STATUS_RUNNING:
        end = now or datetime.now(timezone.utc)
    else:
        end = _log_end_time(log_file) or _parse_iso(job.get("updated_at")) or (now or datetime.now(timezone.utc))
    total_seconds = max(0, int((end - start).total_seconds()))
    return _format_duration(total_seconds)


def _output_file_mtime(output_file):
    """The wall-clock time `output_file` was last written to, read via
    mtime — mirrors `_log_end_time`'s identical use of `os.path.getmtime`
    for `log_file`/`elapsed`. This is a PROXY for "time of the last **step
    update**", not a real per-event timestamp: the **event stream** carries
    none (see the module docstring). `agy` appends each
    NDJSON line to `output_file` as it is produced, so the file's mtime is
    the wall-clock time the most recent line — step update or otherwise —
    was written. Returns `None` if `output_file` is unset or has no mtime
    yet (not created, or created but not yet written to)."""
    if not output_file:
        return None
    try:
        mtime = os.path.getmtime(output_file)
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def _compute_stall(job, status_value, output_file, now=None):
    """`None` unless `status_value` is STATUS_RUNNING and the time since the
    last sign of life exceeds `_STALL_THRESHOLD_SECONDS` — never a status
    transition of its own (STATUS_RUNNING is untouched either way; `cancel`
    and `result` must still be able to find a stalled job by its `status`).

    "Last sign of life" is `_output_file_mtime`'s **step update** arrival
    proxy, falling back to the job's `created_at` when `output_file` has no
    mtime yet — mirroring `_format_elapsed`'s own fallback chain (a job
    that hasn't written any output at all is, at best, only as fresh as
    when it was created). Returns a `_format_duration`-formatted string
    ("how long since the last sign of life"), never a boolean — a caller
    must not be able to mistake "stalled" for a simple flag."""
    if status_value != STATUS_RUNNING:
        return None
    last_activity = _output_file_mtime(output_file) or _parse_iso(job.get("created_at"))
    if last_activity is None:
        return None
    current = now or datetime.now(timezone.utc)
    idle_seconds = max(0, int((current - last_activity).total_seconds()))
    if idle_seconds <= _STALL_THRESHOLD_SECONDS:
        return None
    return _format_duration(idle_seconds)


def _format_duration(total_seconds):
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "{}h {}m".format(hours, minutes)
    if minutes:
        return "{}m {}s".format(minutes, seconds)
    return "{}s".format(seconds)


def _log_tail(log_text, max_chars=_LOG_TAIL_MAX_CHARS):
    lines = [line.strip() for line in log_text.splitlines() if line.strip()]
    if not lines:
        return ""
    tail = lines[-1]
    if len(tail) > max_chars:
        tail = tail[: max_chars - 1] + "\u2026"
    return tail


# --- rendering ---------------------------------------------------------------


def render_status_table(rows, session_scoped=False):
    if not rows:
        scope_note = " for the current session" if session_scoped else ""
        return "No agy jobs{} in this repo yet.".format(scope_note)

    lines = [
        "| Job | Kind | Status | Conversation | Elapsed | Stall | Usage | Log tail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row["id"] or "",
                row["kind"] or "",
                row["status"],
                row["conversation"] or "(none yet)",
                row["elapsed"] or "",
                _format_stall(row["status"], row.get("stall")),
                _format_usage(row.get("usage")),
                _escape_cell(row["log_tail"]),
            )
        )
    return "\n".join(lines)


def _format_stall(status_value, stall):
    """Stall-column placeholder for a falsy `stall`. `_compute_stall`
    returns `None` in two structurally different situations that must not
    look the same to a reader: a `STATUS_RUNNING` **job** with no **step
    update** silence long enough to count as a **stall** yet (still being
    monitored — `"(none yet)"`), versus a terminal **job**, for which a
    **stall** is impossible by definition (no ongoing step updates to go
    silent) — rendered as `"n/a (terminal)"` instead."""
    if stall:
        return stall
    if status_value == STATUS_RUNNING:
        return "(none yet)"
    return "n/a (terminal)"


def _format_usage(usage):
    """Compact one-line rendering of a **result event**'s `usage` dict —
    `None` (no result event yet, i.e. a still-running job) renders as the
    same `"(none yet)"` placeholder `render_status_table` already uses for
    a missing conversation, not a blank cell and not zeros."""
    if not usage:
        return "(none yet)"
    return "in:{} out:{} think:{} cache:{} total:{}".format(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("thinking_tokens", 0),
        usage.get("cache_read_tokens", 0),
        usage.get("total_tokens", 0),
    )


def _escape_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")
