"""companion.result — the `result` subcommand: **harvests** a finished
**job**'s stored final output (its **result**) for this repo.

Harvesting prefers the **event stream**'s **result event** `.response` field
(parsed via `companion.status._parse_event_stream`) and falls back to the raw
captured `output_file` text when no result event is present — a cancelled
job's partial output, or a run whose stream never reached one. The fallback
applies to every job **kind**, not just `review`.

A **result event**'s presence means the run STOPPED, not that it succeeded.
When one is present carrying a `status` other than `"SUCCESS"`, that status
and its human-readable `error` are rendered directly, ahead of both the
`.response` harvest and the raw-text fallback: an ERROR result event's
`response` is empty by definition.

Never re-invokes agy and never re-derives a review from the log. The log is
consulted (via status.build_job_row) only to confirm the job has actually
finished before trusting its output_file.

Ported (logic only, not copied JS) from upstream's
`scripts/lib/job-control.mjs`'s `resolveResultJob`.
"""
import sys
from pathlib import Path

from companion import repo as repo_arg
from companion import state
from companion.git import GitTargetError, ensure_git_repository
from companion.review_output import render_review, tolerant_parse
from companion.status import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_ERROR,
    _parse_event_stream,
    build_job_row,
)

HELP = "Show the stored result of a finished agy job for this repo."

# STATUS_ERROR (bare "error") is a foreground `delegate` job's own stored
# terminal status on a nonzero exit (delegate.py's `_run_live_delegate`) —
# distinct from STATUS_SILENT_FALLBACK / STATUS_CRASHED, which are DERIVED
# labels `status.derive_status` computes from the log/event stream and are
# deliberately NOT listed here: ResultForJobStillActiveTest documents that a
# silently-fallen-back or crashed job stays reported as "still active" by
# this command, on purpose. Do not add those to this tuple.
_FINISHED_STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_ERROR)


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "job_id",
        nargs="?",
        default=None,
        help="Job id (or unambiguous prefix) to show. Defaults to the most "
        "recently finished job for this repo.",
    )


# --- job selection -----------------------------------------------------------


def _most_recently_finished(jobs):
    for job in jobs:
        if build_job_row(job)["status"] in _FINISHED_STATUSES:
            return job
    return None


def _not_finished_message(job, row):
    return "Job {} is not finished yet (status: {}). Check /agy:status and try again once it finishes.".format(
        job.get("id"), row["status"]
    )


# --- core --------------------------------------------------------------------


def result_for_job(repo_root, job_id):
    """Select and render the stored result of a finished job. Returns
    (exit_code, message). Never renders an empty clean review for a job
    that is still running, silently fell back, or crashed — that state is
    reported by name instead."""
    jobs = state.list_jobs(repo_root)
    if not jobs:
        return 1, "No agy jobs found for this repo yet."

    if job_id:
        job, error = state.match_job(jobs, job_id)
        if error:
            return 1, error
    else:
        job = _most_recently_finished(jobs)
        if job is None:
            job = jobs[0]  # newest job overall, to report ITS actual state

    row = build_job_row(job)
    if row["status"] not in _FINISHED_STATUSES:
        return 1, _not_finished_message(job, row)

    return 0, _render_result(job)


def _render_result(job):
    """Harvest `job`'s stored **result**: prefer the **event stream**'s
    **result event** `.response` when one is present in `output_file`,
    falling back to the raw captured `output_file` text otherwise (a
    cancelled job's partial output, legacy/malformed content, or a stream
    that never reached a result event).

    A **result event**'s presence means the run STOPPED, not that it
    succeeded (glossary): before harvesting `.response` at all, check
    `status`. When it is present and not `"SUCCESS"` — the generic crashed
    signal, mirroring `status.py`'s own `derive_status`, which never
    special-cases the literal string `"ERROR"` — render the `status` and
    human-readable `error` directly instead of falling through to the raw
    `output_file` text / `tolerant_parse` path below. This applies to every
    job **kind**, not just `review`, since an ERROR **result event**'s
    `response` is empty by definition and there is nothing for
    `tolerant_parse`/`render_review` to usefully do with it."""
    output_file = job.get("output_file")
    text = state.read_file_safe(output_file)
    if not text:
        return "Job {} finished but no stored result was found (output file: {}).".format(
            job.get("id"), output_file or "(none recorded)"
        )

    event_stream = _parse_event_stream(text)

    if event_stream is not None and event_stream.status is not None and event_stream.status != "SUCCESS":
        return "Job {} did not finish successfully (status: {}): {}".format(
            job.get("id"),
            event_stream.status,
            event_stream.error or "(no error message captured)",
        )

    # Truthiness, not `is not None`: a **result event** whose `response` is
    # present but the empty string must fall back to the raw `output_file`
    # text the same way an absent (`None`) response already does — an empty
    # string is not a real harvested result, and rendering it verbatim
    # (or, for a review, feeding it through tolerant_parse) previously
    # produced an empty review printed with exit 0.
    if event_stream is not None and event_stream.response:
        harvested = event_stream.response
    else:
        harvested = text

    if job.get("kind") == "review":
        return render_review(tolerant_parse(harvested))
    return harvested


def run(args):
    try:
        repo_root = ensure_git_repository(repo_arg.resolve_repo(args))
    except GitTargetError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    exit_code, message = result_for_job(repo_root, args.job_id)
    print(message)
    return exit_code
