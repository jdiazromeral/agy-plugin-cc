"""companion.cancel — the `cancel` subcommand: terminates a running
background **job** by its recorded PID/process group and transitions its
**job** record to a terminal `cancelled` status.

There is no babysitter process (see status.py's module docstring), so
"running" is only ever a log-derived guess — cancellation is the one place
that state actually changes: we signal the recorded `pid`, then write the
job record's `status` to `cancelled` so `/agy:status` (via
`status.build_job_row`'s STATUS_CANCELLED short-circuit) renders it
correctly from then on, no matter what the log itself later shows.

Ported (logic only, not copied JS) from upstream's
`scripts/lib/job-control.mjs`'s `resolveCancelableJob` and
`scripts/lib/process.mjs`'s `terminateProcessTree`.
"""
import os
import signal
import sys
import time

from companion import repo as repo_arg
from companion import state
from companion.git import GitTargetError, ensure_git_repository
from companion.status import STATUS_CANCELLED, STATUS_RUNNING, build_job_row

HELP = "Cancel a running background agy job for this repo."

# How long agy gets to shut down politely before we stop asking. Measured
# against a real 1.1.8 killed mid-stream: 0.16s from SIGTERM to exit (see
# AGENTS.md). 5s is ~30x that — headroom for a loaded machine, not a guess
# at the typical case. Small enough that /agy:cancel still feels immediate.
_SIGTERM_GRACE_SECONDS = 5
# After SIGKILL there is nothing left to negotiate; this only covers the
# kernel's own teardown.
_SIGKILL_GRACE_SECONDS = 2
_POLL_INTERVAL_SECONDS = 0.1


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "job_id",
        nargs="?",
        default=None,
        help="Job id (or unambiguous prefix) to cancel. Defaults to this "
        "session's single active job.",
    )


# --- the mockable kill seam --------------------------------------------------


def _signal(pid, sig):
    """Signal `pid`'s process group first (agy is spawned with
    start_new_session=True, so pid == its own process group leader),
    falling back to the single process if that group is already gone.
    A stale PID — the process (and its group) already exited — is treated
    as success, never a crash: ProcessLookupError (ESRCH) is swallowed in
    both cases, mirroring upstream's terminateProcessTree."""
    try:
        os.killpg(pid, sig)
        return
    except ProcessLookupError:
        pass
    except PermissionError:
        # Not ours to signal. Not an error to raise here — _await_exit will
        # observe that it is still alive and the caller will report that
        # honestly, rather than crashing mid-cancel.
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate(pid):
    """The polite request. Kept as its own seam because tests mock it."""
    _signal(pid, signal.SIGTERM)


def _is_alive(pid):
    """Signal 0 probes for existence without delivering anything. EPERM
    (the process exists but belongs to someone else) counts as alive —
    it cannot be ours to kill, but it is emphatically not gone."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _await_exit(pid, timeout, is_alive=None, sleep=time.sleep):
    """Poll until `pid` is gone or `timeout` seconds elapse. Returns True if
    it exited. Polling (rather than waitpid) because the job is detached and
    was never our child — there is nothing to reap, only to observe.

    `is_alive` defaults to None and is resolved to the module-level probe on
    each call, NOT captured as a default argument value. A default of
    `is_alive=_is_alive` would bind the original function object at def time,
    so patching `cancel._is_alive` would silently have no effect here — the
    seam would look injectable and not be. That cost a red CI run."""
    if is_alive is None:
        is_alive = _is_alive
    deadline = time.monotonic() + timeout
    while True:
        if not is_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(_POLL_INTERVAL_SECONDS)


# --- job selection -----------------------------------------------------------


def _select_job(repo_root, jobs, job_id):
    if job_id:
        return state.match_job(jobs, job_id)

    session_id = os.environ.get(state.SESSION_ID_ENV)
    active = [job for job in jobs if build_job_row(job)["status"] == STATUS_RUNNING]
    scoped = [job for job in active if job.get("session_id") == session_id] if session_id else active

    if len(scoped) == 1:
        return scoped[0], None
    if len(scoped) > 1:
        return None, "Multiple agy jobs are active. Pass a job id to /agy:cancel."
    if session_id:
        return None, "No active agy jobs to cancel for this session."
    return None, "No active agy jobs to cancel."


# --- core --------------------------------------------------------------------


def _not_running_message(job_id, status):
    return "Job {} is not running (status: {}) — nothing to cancel.".format(job_id, status)


def _cancel_if_still_running(repo_root, job):
    """Re-derive `job`'s **status** FRESH off disk — never the stale `row`
    read at `cancel_job`'s entry — immediately before writing `cancelled`.

    A **result event** can land in the job's `output_file` after the entry
    check saw it as running but before this point: the process was already
    finishing when SIGTERM arrived, wrote its result event, and then exited
    (from the signal or on its own) during `_await_exit`'s poll window.
    `build_job_row` always re-reads `output_file` fresh (no caching), so
    calling it again here — right at the write, not before — is what
    catches that. Every call site that writes `STATUS_CANCELLED` must go
    through this, not just the entry-time fast path (which only proves the
    job wasn't ALREADY finished when cancel_job started).

    Returns (wrote_cancelled, fresh_row): if the job still derives as
    running, writes `cancelled` and returns (True, fresh_row); otherwise
    leaves the now-terminal status untouched and returns (False, fresh_row)
    so the caller can report that actual status instead."""
    fresh_row = build_job_row(job)
    if fresh_row["status"] != STATUS_RUNNING:
        return False, fresh_row
    state.upsert_job(repo_root, {"id": job["id"], "status": STATUS_CANCELLED})
    return True, fresh_row


def cancel_job(
    repo_root,
    job_id,
    terminate=_terminate,
    await_exit=_await_exit,
    force=_signal,
):
    """Cancel the selected **job**. Returns (exit_code, message).

    An already-terminal job (completed, cancelled, crashed, or silently
    fell back) is a clean no-op, exit 0 — cancel never kills or errors on
    a job that has already finished one way or another."""
    jobs = state.list_jobs(repo_root)
    job, error = _select_job(repo_root, jobs, job_id)
    if error:
        return 1, error

    row = build_job_row(job)
    if row["status"] != STATUS_RUNNING:
        return 0, _not_running_message(job["id"], row["status"])

    pid = job.get("pid")
    if pid is None:
        wrote, fresh_row = _cancel_if_still_running(repo_root, job)
        if not wrote:
            return 0, _not_running_message(job["id"], fresh_row["status"])
        return 0, (
            "Cancelled job {} (no PID was recorded for it, so the process "
            "could not be signalled directly).".format(job["id"])
        )

    # SIGTERM is a request, not an outcome. Marking the job `cancelled`
    # before confirming the process died would be the worst possible lie
    # here: `cancelled` short-circuits status rendering (see build_job_row),
    # so a process that ignored the signal would keep running, keep spending
    # quota, and never appear in /agy:status again. Verify, then report.
    terminate(pid)
    if not await_exit(pid, _SIGTERM_GRACE_SECONDS):
        force(pid, signal.SIGKILL)
        if not await_exit(pid, _SIGKILL_GRACE_SECONDS):
            # Deliberately NOT marked cancelled: the job is still running, and
            # status must keep saying so.
            return 1, (
                "error: job {} did not exit — its process (pid {}) survived "
                "SIGTERM and SIGKILL. It is still running and may still be "
                "spending agy quota; the job has been left as running rather "
                "than reported cancelled. Investigate pid {} directly.".format(
                    job["id"], pid, pid
                )
            )
        wrote, fresh_row = _cancel_if_still_running(repo_root, job)
        if not wrote:
            return 0, _not_running_message(job["id"], fresh_row["status"])
        return 0, (
            "Cancelled job {} (it ignored SIGTERM for {}s and was killed).".format(
                job["id"], _SIGTERM_GRACE_SECONDS
            )
        )

    wrote, fresh_row = _cancel_if_still_running(repo_root, job)
    if not wrote:
        return 0, _not_running_message(job["id"], fresh_row["status"])
    return 0, "Cancelled job {}.".format(job["id"])


def run(args):
    try:
        repo_root = ensure_git_repository(repo_arg.resolve_repo(args))
    except GitTargetError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    exit_code, message = cancel_job(repo_root, args.job_id)
    print(message)
    return exit_code
