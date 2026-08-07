"""companion.delegate — hand a task to `agy`'s default agent, write-capably,
foreground or `--background`, with `--conversation` **resume** of the
repo's last **delegate** **conversation**.

Unlike `/agy:review`, delegate never passes `--agent`, and on a fresh run it
forwards agy's stdout verbatim: there is no schema to parse and a delegated
task's output is free text.

The dangerous edge is **resume**. `--conversation <uuid>` genuinely resumes
when the uuid is known, but silently falls back to a brand-new conversation
when it is not — exit 0, clean stdout, empty stderr (see AGENTS.md). A resume
launch therefore adds `--output-format stream-json` and confirms the **bind**
structurally: the requested uuid against the parsed **event stream**'s
**result event** `conversation_id`. Never a regex match on `--log-file` prose,
which rewording, localization, or `--output-format` suppression all defeat.
It fails closed — an unparseable stream, or one that never reaches a result
event, is never a confirmed bind — and a **silent fallback**'s new
conversation is never persisted as this repo's last delegate conversation.
On a confirmed bind it is the **result event**'s `response` that reaches the
user's stdout, not the raw NDJSON.

A **background launch** always requests `--output-format stream-json`:
nothing waits on the detached process, so its captured `output_file` is all
`/agy:status` and `/agy:result` have to read.

The foreground fresh path has no intended uuid to compare against, so it
resolves its conversation from `--log-file` instead
(`_log_conversation_if_bound`), as does `_resolve_last_conversation`'s
background-job recovery fallback — neither has an event stream to parse.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from companion import repo as repo_arg
from companion import state, stream_events
from companion.agy_log import find_conversation
from companion.git import GitTargetError, ensure_git_repository
from companion.launch import (
    _AGY_TIMEOUT_SECONDS,
    _BACKGROUND_PRINT_TIMEOUT_ARG,
    _print_timeout_arg,
    _spawn_detached,
    positive_int_timeout,
)

HELP = "Hand a task to agy's default agent, foreground or --background, fresh or --resume."

# A delegate-specific trace pattern, deliberately NOT added to
# companion.agy_log — this pattern is delegate-only.
#
# Not the foreground resume **bind** check: that path compares the requested
# uuid against the **event stream**'s **result event** `conversation_id`
# structurally (module docstring). Two callers keep this pattern alive, and
# neither has a structured equivalent:
#
#   1. `_log_conversation_if_bound` (below), reached from the fresh launch
#      path and from `_resolve_last_conversation`'s background-job recovery
#      fallback. A background resume that silently fell back stores
#      `conversation: None` and bind-checks nothing, so this regex is the
#      only thing stopping the NEXT `--resume` from resuming the **silent
#      fallback**'s wrong conversation. The event stream cannot answer this:
#      no *requested* uuid is persisted anywhere to compare a later
#      `conversation_id` against.
#   2. `tools/live_delegate_capture.probe_resume_fallback`, the whole of
#      `make check-live-free`. That probe is free precisely because
#      `--model bogus-model-xyz` makes agy exit before any conversation is
#      created — so there is no **init event** and no **result event** to
#      compare, and the log trace is the only zero-quota signal that agy
#      still announces a fallback at all.
#
# agy 1.1.8 emits TWO lines for a failed resume (captured verbatim in
# tests/fixtures/delegate/2026-07-30-resume-fallback.log):
#
#   W... common.go:281] Conversation <uuid> not found, ignoring --conversation flag
#   Warning: conversation "<uuid>" not found.
#
# The uuid is quoted in the second (human-facing) line and bare in the first
# (klog) line. An earlier version of this pattern required the quotes, so it
# matched ONLY the human-facing line — leaving the plugin's most dangerous
# guard resting on presentation text, which is the likeliest thing to be
# reworded or suppressed (e.g. under --output-format json), while the durable
# klog trace slipped past. Quoting is optional here so BOTH lines match and
# either one alone is enough to detect the fallback.
CONVERSATION_NOT_FOUND_RE = re.compile(r'[Cc]onversation "?([0-9a-fA-F-]+)"? not found')


class DelegateLaunchError(Exception):
    """The foreground `agy` launch could not even start (binary missing,
    timed out)."""


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "task",
        nargs="+",
        help="The task to hand to agy. Captures the rest of the command line as one string.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Launch agy detached and return immediately, printing the job id instead of "
        "waiting for and forwarding its output. Check progress with /agy:status.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume this repo's last delegate conversation instead of starting a new one. "
        "A clean error if there is no prior delegate conversation.",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="Passthrough --model to agy. Not forwarded unless supplied.",
    )
    parser.add_argument(
        "--effort",
        default=None,
        metavar="EFFORT",
        help="Passthrough --effort to agy. Not forwarded unless supplied.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int_timeout,
        default=None,
        metavar="SECONDS",
        help="Foreground subprocess timeout in seconds (default: {}). Rejected together "
        "with --background, which has no timeout ceiling by design. Not forwarded unless "
        "supplied.".format(_AGY_TIMEOUT_SECONDS),
    )


def run(args):
    if args.timeout is not None and args.background:
        print(
            "error: --timeout cannot be combined with --background: background runs have "
            "no timeout ceiling by design.",
            file=sys.stderr,
        )
        return 1

    task = " ".join(args.task)
    try:
        repo_root = ensure_git_repository(repo_arg.resolve_repo(args))
    except GitTargetError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    conversation = None
    if args.resume:
        conversation = _resolve_last_conversation(repo_root)
        if conversation is None:
            print(
                "error: no prior delegate conversation found for this repo; "
                "omit --resume to start a fresh one.",
                file=sys.stderr,
            )
            return 1

    if args.background:
        return _run_background_delegate(repo_root, task, args.model, args.effort, conversation)
    timeout = args.timeout if args.timeout is not None else _AGY_TIMEOUT_SECONDS
    return _run_live_delegate(
        repo_root, task, args.model, args.effort, conversation, timeout=timeout
    )


# --- command vector ----------------------------------------------------


def _agy_command(task, log_file, model=None, effort=None, conversation=None, print_timeout=None):
    """Build the `agy` command vector shared by the foreground and
    background delegate paths. Fresh: `--new-project`. Resume:
    `--conversation <uuid>` REPLACES `--new-project` at the same position.
    `task` is verbatim user text from `/agy:delegate <task>`, so
    `--disable-slash-commands` is always present — agy resolves slash
    commands in print mode from 1.1.9 on, and every supported binary is
    above that (`setup.MIN_AGY_VERSION`), so a prompt beginning with `/`
    would otherwise be resolved as **slash-command expansion** instead of
    being sent as literal text. Unconditional, no version check. Pure —
    no I/O — so it is trivial to assert on directly in a test.

    `print_timeout`, when given, is appended as `--print-timeout
    <print_timeout>` right after `--log-file` — the pre-computed argument
    STRING (see `companion.launch._print_timeout_arg` and
    `_BACKGROUND_PRINT_TIMEOUT_ARG`), never a bare number. `None` (the
    default) omits the flag entirely, which is what keeps this function's
    OWN pure-vector tests unchanged — every real caller (`_resume_agy_command`,
    `_background_agy_command`) always supplies one; only a bare direct call
    (as in `tests/test_delegate.py`'s `CommandVectorTest`) legitimately
    omits it."""
    cmd = [
        "agy", "-p", task,
        "--disable-slash-commands",
        "--dangerously-skip-permissions", "--sandbox",
    ]
    if conversation:
        cmd += ["--conversation", conversation]
    else:
        cmd += ["--new-project"]
    cmd += ["--log-file", str(log_file)]
    if print_timeout:
        cmd += ["--print-timeout", print_timeout]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _resume_agy_command(
    task, log_file, model=None, effort=None, conversation=None, timeout=_AGY_TIMEOUT_SECONDS
):
    """`_agy_command`'s vector, plus `--print-timeout` (derived from
    `timeout`, the subprocess timeout this same launch will run under — see
    `companion.launch._print_timeout_arg`) and, when `conversation` is set,
    `--output-format stream-json` so the live resume path has an **event
    stream** to structurally bind-check against. This wrapper is the only
    thing `_run_live_delegate` calls; `_agy_command` itself is untouched —
    a bare call to it still omits both flags, which is why its own
    pure-vector tests need no change."""
    cmd = _agy_command(
        task, log_file, model=model, effort=effort, conversation=conversation,
        print_timeout=_print_timeout_arg(timeout),
    )
    if conversation:
        cmd = cmd + ["--output-format", "stream-json"]
    return cmd


def _background_agy_command(task, log_file, model=None, effort=None, conversation=None):
    """`_agy_command`'s vector plus `--print-timeout
    _BACKGROUND_PRINT_TIMEOUT_ARG` and `--output-format stream-json`,
    ALWAYS — fresh or resume.

    The stream-json flag: a **background launch**'s stdout is captured to
    the job's `output_file` and read back by `companion.status`, which
    derives a job's status purely from the **event stream**'s **result
    event**. Without the flag that file holds plain text,
    `status.derive_status` finds no result event, and every background
    delegate job reads `running` forever. Mirrors `review._agy_command`,
    which already requests the stream on both its foreground and background
    launches.

    The print-timeout flag: a **background launch** is documented as having
    no timeout ceiling by design (see the `--timeout` + `--background`
    rejection in `run()`), but agy's OWN `--print-timeout` defaults to
    `5m0s` regardless — leaving it unset would silently reimpose that
    5-minute ceiling. `_BACKGROUND_PRINT_TIMEOUT_ARG` (`companion.launch`)
    is the large, explicit, VERIFIED-to-parse stand-in for "no ceiling"."""
    return _agy_command(
        task, log_file, model=model, effort=effort, conversation=conversation,
        print_timeout=_BACKGROUND_PRINT_TIMEOUT_ARG,
    ) + ["--output-format", "stream-json"]


# --- foreground live launch ---------------------------------------------


def _launch_agy(repo_root, cmd, log_file, timeout=_AGY_TIMEOUT_SECONDS):
    """Launch `cmd`, cwd=repo_root, blocking. Returns (result, log_text).
    Raises DelegateLaunchError if agy itself cannot be found or times out."""
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise DelegateLaunchError(
            "agy is not installed or not on PATH. Run `/agy:setup` to check."
        )
    except subprocess.TimeoutExpired:
        raise DelegateLaunchError("agy timed out after {}s".format(timeout))

    log_path = Path(log_file)
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    return result, log_text


def _run_live_delegate(repo_root, task, model, effort, conversation, timeout=_AGY_TIMEOUT_SECONDS):
    """Launch `agy` once as a blocking foreground run against a persistent
    job log (unlike review's ephemeral temp log — a delegate conversation
    must be recoverable for a later --resume). A resume launch (`conversation`
    truthy) requests `--output-format stream-json` and, on a confirmed
    **bind**, forwards the parsed **event stream**'s **result event**
    `response` to the user rather than the raw NDJSON; a fresh launch is
    untouched — same command vector, same plain stdout forwarded verbatim.
    Either way, on a confirmed non-fallback finish, the forwarded text is
    ALSO persisted to an **output file** (`state.resolve_job_output_file`)
    and recorded as `output_file` on the job record, so a finished
    foreground delegate job is harvestable via `/agy:result` instead of
    being a **zombie job** — the same `output_file` shape the background
    launch already writes. The **silent fallback** early return below is
    exempt: it forwards nothing and already stores its own terminal status.
    Stdout is unchanged either way: `sys.stdout` still gets exactly the
    forwarded bytes, verbatim and in full. Returns the process exit code."""
    job_id = state.generate_job_id("delegate")
    log_file = state.resolve_job_log_file(repo_root, job_id)
    cmd = _resume_agy_command(
        task, log_file, model=model, effort=effort, conversation=conversation, timeout=timeout
    )

    try:
        result, log_text = _launch_agy(repo_root, cmd, log_file, timeout=timeout)
    except DelegateLaunchError as exc:
        # A timeout means real time (and for a real `agy`, real quota) was
        # already spent — record the attempt rather than let it vanish.
        # `"error: ..."` is trusted directly by status._is_stored_terminal_status,
        # the same shape delegate.py's own silent-fallback branch below uses.
        state.upsert_job(repo_root, {
            "id": job_id,
            "kind": "delegate",
            "status": "error: {}".format(exc),
            "log_file": str(log_file),
            "conversation": None,
            "session_id": os.environ.get(state.SESSION_ID_ENV),
        })
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    if conversation:
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        event_stream = _parse_event_stream_safely(stdout_text)
        bound, bind_proof = _resume_bind_check(event_stream, conversation)
        if not bound:
            # The wrong new conversation a silent fallback created is never
            # persisted as this job's conversation — see module docstring.
            state.upsert_job(repo_root, {
                "id": job_id,
                "kind": "delegate",
                "status": "error: silent fallback",
                "log_file": str(log_file),
                "conversation": None,
                "session_id": os.environ.get(state.SESSION_ID_ENV),
            })
            print(
                "error: SILENT FALLBACK — agy did not resume conversation \"{}\"; the "
                "event stream shows {} instead. Refusing to render this as a "
                "successful resume.".format(conversation, bind_proof),
                file=sys.stderr,
            )
            return 1
        resolved_conversation = conversation
        forwarded_stdout_text = event_stream.response or ""
    else:
        resolved_conversation = _log_conversation_if_bound(log_text)
        forwarded_stdout_text = result.stdout.decode("utf-8", errors="replace")

    output_file = state.resolve_job_output_file(repo_root, job_id)
    output_file.write_text(forwarded_stdout_text, encoding="utf-8")

    state.upsert_job(repo_root, {
        "id": job_id,
        "kind": "delegate",
        "status": "completed" if result.returncode == 0 else "error",
        "log_file": str(log_file),
        "output_file": str(output_file),
        "conversation": resolved_conversation,
        "session_id": os.environ.get(state.SESSION_ID_ENV),
    })

    sys.stdout.write(forwarded_stdout_text)
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    if stderr_text:
        sys.stderr.write(stderr_text)
    return result.returncode


# --- background launch ---------------------------------------------------


def _run_background_delegate(repo_root, task, model, effort, conversation, spawn=_spawn_detached):
    """Launch `agy` detached — reusing M5's `companion.launch._spawn_detached`
    seam — writing to a persistent `--log-file` under the repo's state dir, and
    record the delegate job immediately without blocking on the child.
    `conversation` is stored as None even for a resume launch: nothing has
    bind-checked it yet, and a later /agy:status or --resume read derives
    the real, guarded conversation from the log itself (see
    _resolve_last_conversation) rather than trusting an unverified guess
    recorded at launch time.

    Mirrors review.py's `_run_background_review` — the detached
    agy's stdout is captured to a persistent `output_file` (otherwise lost
    to DEVNULL for good, since there is no babysitter) so `/agy:result` can
    later harvest a finished background delegate job's stored **result**,
    and the spawned process's `pid` is recorded so `/agy:cancel` can
    actually signal it. Both are required of every background launch;
    tests/test_cross_command_integration.py holds the two paths to the same
    shape."""
    job_id = state.generate_job_id("delegate")
    log_file = state.resolve_job_log_file(repo_root, job_id)
    output_file = state.resolve_job_output_file(repo_root, job_id)
    cmd = _background_agy_command(
        task, log_file, model=model, effort=effort, conversation=conversation
    )

    try:
        proc = spawn(cmd, repo_root, stdout_path=str(output_file))
    except FileNotFoundError:
        print(
            "error: agy is not installed or not on PATH. Run `/agy:setup` to check.",
            file=sys.stderr,
        )
        return 1

    # Stub spawns in tests return None or a bare object; a real
    # Popen always has .pid. getattr with a default keeps both shapes safe.
    pid = getattr(proc, "pid", None)

    state.upsert_job(repo_root, {
        "id": job_id,
        "kind": "delegate",
        "status": "running",
        "log_file": str(log_file),
        "output_file": str(output_file),
        "pid": pid,
        "conversation": None,
        "session_id": os.environ.get(state.SESSION_ID_ENV),
    })
    print(
        "Launched background delegate job {} (log: {}). Check progress with "
        "/agy:status.".format(job_id, log_file)
    )
    return 0


# --- resume resolution + bind check --------------------------------------


def _log_conversation_if_bound(log_text):
    """The conversation this log shows as genuinely bound — never the
    conversation a silent fallback created instead. None if the log shows
    no conversation yet, or shows the "not found" trace (in which case
    whatever conversation it went on to create must never be trusted)."""
    if CONVERSATION_NOT_FOUND_RE.search(log_text):
        return None
    return find_conversation(log_text)


def _parse_event_stream_safely(stdout_text):
    """**Tolerant parse** of `stdout_text` (a resume launch's raw stdout,
    expected to be the NDJSON **event stream** under `--output-format
    stream-json`) via `stream_events.parse_event_stream`. Returns `None`
    on any parse failure — malformed or truncated NDJSON raises
    `json.JSONDecodeError`, a `ValueError` subclass — rather than letting a
    parse failure crash the run; `_resume_bind_check` then fails closed on
    `None` exactly as it does on a stream with no result event."""
    try:
        return stream_events.parse_event_stream(stdout_text)
    except ValueError:
        return None


def _resume_bind_check(event_stream, intended_uuid):
    """Confirm a resume run actually bound `intended_uuid`: a structural
    identity comparison of the intended uuid against the parsed **event
    stream**'s **result event** `conversation_id` — never a regex match on
    log or stdout text. Fails closed: `event_stream` being `None`
    (unparseable stdout) or never reaching a **result event** (`status` and
    `conversation_id` both still unset — crash, timeout, truncated capture)
    is never treated as a confirmed bind; "cannot establish" is not a pass.
    Returns (bound, proof)."""
    if event_stream is None or event_stream.status is None or event_stream.conversation_id is None:
        return False, "(no result event in the event stream)"
    if event_stream.conversation_id == intended_uuid:
        return True, event_stream.conversation_id
    return False, event_stream.conversation_id


def _resolve_last_conversation(repo_root):
    """The repo's last delegate conversation: the most recent delegate-kind
    job's persistent --log-file, parsed for the conversation it genuinely
    bound. Never ~/.gemini/.../cache/last_conversations.json — the per-job
    state dir is the source of truth. None if there is no delegate job yet,
    or its log shows no genuinely bound conversation (including a job whose
    only resume attempt silently fell back)."""
    for job in state.list_jobs(repo_root):
        if job.get("kind") != "delegate":
            continue
        stored = job.get("conversation")
        if stored:
            return stored
        return _log_conversation_if_bound(state.read_file_safe(job.get("log_file")))
    return None
