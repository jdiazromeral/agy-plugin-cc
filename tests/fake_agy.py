#!/usr/bin/env python3
"""fake agy — a stub `agy` executable injected on PATH for tests.

This is a fake agy, not a mock: it is a real executable that the companion
invokes via subprocess exactly as it would invoke the real `agy` binary.
Behavior is switched by the FAKE_AGY_BEHAVIOR environment variable so one
stub covers every case the setup doctor's and the foreground review path's
tests need. It never touches the network and never shells out to the real
`agy`.

test_setup.py and test_review_live.py both copy this file's body into a temp
directory as `agy`, rewriting the shebang to the interpreter running the
tests (see _install_fake_agy in each) so it never depends on PATH to find
python3 itself.

Review-launch behaviors (dispatched off the `-p` subcommand, mirroring
tools/live_review_capture.py's proven `agy -p ... --disable-slash-commands
--agent agy-review --sandbox --new-project --print-timeout <T>
--log-file <path>` invocation). Both this shape and the delegate-launch
shape below always carry `--disable-slash-commands` AND `--print-timeout` —
an argv missing either is refused loudly by
`_reject_missing_disable_slash_commands` / `_reject_missing_print_timeout`
before any behavior below runs (same discipline as
`_requests_stream_json`/`_stdout_for`). `--print-timeout` exists so agy's
own print-mode wait timer always expires BEFORE the plugin's subprocess
timeout — see `companion.launch`'s module docstring (mission
agy-1111-free-probes); this fake does not otherwise read its value, only
that it is present:

  - "review_bound_valid" (default for `-p`): writes a `--log-file` with a
    `Created conversation <uuid>` line and no fallback line (bound), and
    emits an **event stream** on stdout — since `companion.review._agy_command`
    now always requests `--output-format stream-json`, this fake must emit a
    stream-json-shaped stdout to keep standing in credibly for the real
    binary (see AGENTS.md's warning about a fake whose shape silently
    diverges from the real binary). Two sources, checked in this order:
      - FAKE_AGY_STREAM_PATH, if set: that file's bytes are written to
        stdout UNWRAPPED, byte-for-byte — for replaying a real, already
        event-stream-shaped capture (e.g.
        tests/fixtures/adversarial_review/) verbatim, so a test proves the
        real parse path against real bytes rather than this fake's own
        synthetic wrapping.
      - otherwise: the bytes at FAKE_AGY_STDOUT_PATH (so a test can replay a
        real *response*-only fixture, e.g. tests/fixtures/review/, without
        duplicating it) or a small built-in valid review JSON, wrapped by
        _stream_json_wrap() into a minimal synthesized `init` + `result`
        event stream (`response` = that exact text) before being written to
        stdout, so it round-trips correctly through the real
        `parse_event_stream`-based extraction.
  - "review_stream_garbage": writes a bound `--log-file`, but stdout is text
    that is not a valid **event stream** at all (not even one parseable
    NDJSON line) — models a stream that fails to parse outright; the live
    path must fall back to treating this raw text as agy's stdout rather
    than raising.
  - "review_stream_no_result": writes a bound `--log-file`, and stdout is a
    genuine but truncated **event stream** — an **init event** and a
    **step update**, then nothing — never reaching a **result event**;
    models a mid-stream crash. The live path must fall back to the raw
    stdout text rather than treating a missing `response` as empty output.
  - "review_silent_fallback": writes a `--log-file` with the
    `Agent "..." not found, falling back to default` line — the run did NOT
    bind agy-review — plus unrelated stdout, exit 0, no stderr signal.
  - "review_bound_crashed": writes a bound log (no fallback line) but exits
    1 with empty stdout, mirroring M2 ledger row 3 (Finding C).
  - "review_background_finished": like "review_bound_valid", but the
    written log also carries the **completion marker**
    (`Stream completed for <uuid>, clearing ResponsePending`) — the
    "finished" state a **background launch**'s persistent log reaches once
    agy is done, which "review_bound_valid" alone never produces (M4's
    foreground path never needed to detect it). Exits 0 immediately so
    background-launch integration tests stay fast.
  - "review_unknown_bind": writes an EMPTY `--log-file` — no fallback line,
    no `Created conversation` line, no `printmode.go` line at all — while
    stdout still looks like a clean review. Models the true "zero evidence"
    case `_bind_check` must report as "unknown", distinct from a confirmed
    "not_bound" fallback; the live review path must never render this
    stdout as a trusted review.

Delegate-launch behaviors (also dispatched off `-p`, but branching on a
`delegate_*` FAKE_AGY_BEHAVIOR value so review's dispatch above is untouched
— delegate never passes `--agent`, and resume passes `--conversation`
instead of `--new-project`):

  - "delegate_fresh_bound": a fresh delegate run (`--new-project`, no
    `--conversation`) — writes a `--log-file` with a
    `Created conversation <uuid>` line, stdout the fake's delegate reply,
    exit 0.
  - "delegate_background_finished": like "delegate_fresh_bound", but the
    log also carries the **completion marker**, and exits immediately —
    mirrors "review_background_finished" for the delegate background path.
  - "delegate_resume_bound": a genuine resume. Still writes the log's
    completion marker for the requested uuid (log-fidelity coverage), but
    the bind check that matters now reads stdout: when the launch's argv
    carries `--output-format stream-json` (every live foreground resume,
    per the M3 mission), emits a minimal init+result **event stream** whose
    **result event** `conversation_id` echoes back the requested
    `--conversation` uuid, `status: "SUCCESS"`, `response` equal to
    `_DELEGATE_STDOUT` — the structural trace the bind check must recognize
    as "the intended conversation actually resumed". Without the flag,
    falls back to the old plain-stdout behavior unchanged.
  - "delegate_resume_silent_fallback": the single most dangerous path this
    mission guards — an unknown `--conversation` value. Still writes the
    log's "not found" + fresh `Created conversation <new-uuid>` prose
    trace, but when `--output-format stream-json` is present the **event
    stream** itself reports `status: "SUCCESS"` with a DIFFERENT
    `conversation_id` (that same freshly-"created" uuid) — exactly the
    silent-fallback signature: agy itself claims success, on the wrong
    conversation, with nothing else signaling anything went wrong. Without
    the flag, falls back to the old plain-stdout behavior unchanged.

Setup bind-probe behaviors: `/agy:setup`'s doctor extends `agy agents` (which
only ever lists GLOBAL custom agents — a workspace-scoped agent like
`agy-review` never appears there) with the free `--model <invalid>` probe
from `docs/review-schema-verdict.md` Finding A. That probe's `-p` invocation
is distinguished from every review/delegate `-p` call by carrying `--model`
— no other `-p` caller in this test suite passes it — so it is dispatched
here BEFORE the review/delegate branch, off a separate
FAKE_AGY_BIND_PROBE_BEHAVIOR env var that never collides with
FAKE_AGY_BEHAVIOR's review_*/delegate_* values. this
branch is also gated on `--disable-slash-commands` via
`_reject_missing_disable_slash_commands`, same discipline as the
review/delegate launches below:

  - "bind_probe_no_fallback" (default): writes a log with `printmode.go`
    agent-resolution and model-validation lines and no fallback line, then
    `--model` fails local validation before any model call — no `Created
    conversation` line is ever written on this path (per Finding A). This is
    the REAL free-probe shape and it is genuinely ambiguous: reaching
    print-mode logging proves print mode ran, not that agy-review resolved
    (that inference is retired — see companion/agy_log.py's PRINTMODE_RE
    comment). `_bind_check` reports "unknown", not "bound" — renamed from
    "bind_probe_bound", which claimed a proof this shape cannot supply.
    Exits 1 with an "invalid model selection" stderr line.
  - "bind_probe_not_bound": writes the same fallback log
    "review_silent_fallback" writes — the agent never bound. Exits 1.
  - "bind_probe_unknown": writes an EMPTY --log-file — zero evidence either
    way, the same shape "review_unknown_bind" exercises on the foreground
    live path. Exits 1.

Read-only slash-command probe behaviors: agy 1.1.11 added non-interactive
answers, in print mode, for a handful of read-only slash commands
(`companion.probe`): `agy -p "/<cmd>" --output-format json [--model M]
[--effort E]`. This vector is deliberately, permanently missing
`--disable-slash-commands` (see companion/probe.py's module docstring for
why that is correct, not a bug) — every OTHER `-p` vector in this fake
still goes through `_reject_missing_disable_slash_commands` and is refused
loudly without it, per that function's own docstring; this is the ONE
carve-out, and it is narrow on purpose. `_is_read_only_probe_vector` only
recognizes a `-p` call whose prompt starts with "/" AND that requests
`--output-format json` — the two properties together are what no other `-p`
vector in this fake produces (review/delegate prompts are never
plugin-authored constants starting with "/", and neither ever requests
plain `--output-format json`). That check runs BEFORE the `--model`
dispatch below, because `companion.probe._probe_command` can also forward
`--model`/`--effort` (that is the whole point of the model-forwarding
proof `setup._probe_model_forwarding` runs) — without checking the probe
shape first, a forwarding probe would be misrouted into
`_bind_probe_launch`, which expects a "ping" prompt and an intentionally-
invalid model. Dispatches on FAKE_AGY_PROBE_BEHAVIOR, a third env var
independent of FAKE_AGY_BEHAVIOR and FAKE_AGY_BIND_PROBE_BEHAVIOR:

  - "probe_default" (the default): a well-behaved agy. `/model` answers
    with whatever `--model`/`--effort` was forwarded, or a fixed
    "currently selected" model/effort when neither was forwarded — the
    faithful round-trip `_probe_model_forwarding` reports as "confirmed".
    `/usage` answers with a canned two-group envelope copied from a real
    1.1.11 capture, including one bucket that carries the OPTIONAL
    `description` field and one that omits it — both real shapes, not
    invented for symmetry. `/credits`, `/effort`, `/skills` answer with
    small stub payloads (no test currently asserts on their shape).
  - "probe_mismatch": models the exact defect `_probe_model_forwarding`
    exists to catch. A bare `/model` probe (no `--model` forwarded)
    answers with one fixed model/effort; a probe that DOES forward
    `--model`/`--effort` ignores what was asked for and answers with a
    DIFFERENT fixed model/effort instead — a silent fallback, agy's own
    words, no error, no signal. `setup._probe_model_forwarding` must
    report this as "mismatch", loudly.
  - "probe_ignores_model": a DIFFERENT, more dangerous shape than
    "probe_mismatch" above — this behavior answers with the SAME fixed
    model/effort NO MATTER what `--model` was (or was not) forwarded. This
    is the shape that broke the first version of
    `setup._probe_model_forwarding`: that version forwarded the SAME value
    it had just read as the baseline, so against a binary that genuinely
    ignores `--model` and just keeps answering with its persisted default,
    "ignored" and "honored" looked byte-identical and the tautological
    check reported "confirmed". The fixed version always forwards a value
    that DIFFERS from the baseline (chosen from `agy models`, see below),
    so run against THIS behavior it must report "mismatch" — never
    "confirmed". See `tests/test_setup.py`'s
    `IgnoredModelForwardingIsCaughtTest`, the regression guard for exactly
    this.
  - "probe_error": exits 1 with a stderr line and no stdout — models agy
    refusing the probe outright (e.g. malformed flags on some future
    version). Must be read as "unknown", never a guess.
  - "probe_garbage_json": exits 0, but stdout is not JSON at all. Must be
    "unknown", not a crash.
  - "probe_missing_command": exits 0 with a syntactically valid JSON
    envelope that carries no `"command"` key at all — models a future agy
    reshaping the envelope. Must be "unknown".
  - "probe_wrong_name": exits 0 with a valid envelope whose
    `command.name` does NOT match the command that was actually
    requested. Must be "unknown" — `_run_probe` must never trust a
    payload it did not verify answers the question it asked.

`agy models` behaviors: a real, separate subcommand (`agy models`, no `-p`
at all) that `setup._probe_model_forwarding` uses to pick a probe target
guaranteed to differ from the baseline `/model` answer — see that
function's docstring for why forwarding the SAME value the baseline read
proves nothing. Dispatched off FAKE_AGY_MODELS_BEHAVIOR, documented above
`_models_launch` in this file: "models_ok" (default, the real 1.1.11
tab-separated shape), "models_fails" (exit 1), "models_garbage" (exit 0,
no parseable lines), "models_too_few" (exit 0, exactly one distinct
model — not enough to discriminate against any baseline).

Version behavior: FAKE_AGY_VERSION overrides the `agy --version` string
(default "1.1.6", below the doctor's 1.1.11 floor) so setup.py's
version-floor check can be exercised on both sides without a real binary.
"""
import json
import os
import sys

BEHAVIOR = os.environ.get("FAKE_AGY_BEHAVIOR", "authenticated_with_agents")


def _stream_json_result(conversation_id, response_text):
    """Wrap `response_text` in a minimal, realistic **event stream**
    (an **init event** then a **result event**, `result.status` "SUCCESS",
    `result.response` the exact same text as before) instead of writing it
    raw. Used only by the two **background launch** behaviors below whose
    stdout is captured to a job's `output_file` and read back by
    companion.status/companion.result
    contract, "The cross-mission gap this contract resolves"."""
    init_event = json.dumps({"event": "init", "conversation_id": conversation_id})
    result_event = json.dumps(
        {
            "event": "result",
            "result": {
                "conversation_id": conversation_id,
                "status": "SUCCESS",
                "response": response_text,
            },
        }
    )
    return init_event + "\n" + result_event + "\n"


def _requests_stream_json(argv):
    for i, arg in enumerate(argv):
        if arg == "--output-format" and i + 1 < len(argv) and argv[i + 1] == "stream-json":
            return True
    return False


def _disables_slash_commands(argv):
    return "--disable-slash-commands" in argv


def _reject_missing_disable_slash_commands(argv):
    """`delegate._agy_command` and `review._agy_command` send
    `--disable-slash-commands` on EVERY review-launch and delegate-launch
    they build, unconditionally. An argv reaching `_review_launch` or
    `_delegate_launch` without it is a shape the real companion-built
    command vector would never produce, so — same discipline as
    `_requests_stream_json`/`_stdout_for` — this fake refuses to stand in
    for it credibly rather than grading the companion against itself. Loud,
    not silent: a stderr line and a non-zero exit, never a quiet fallback.
    Returns True (and writes nothing) when the flag is present."""
    if _disables_slash_commands(argv):
        return True
    sys.stderr.write(
        "fake agy: argv is missing --disable-slash-commands — the real "
        "companion-built command vector always sends it on this launch "
        "shape; refusing to stand in for one that would never happen\n"
    )
    return False


def _carries_print_timeout(argv):
    return "--print-timeout" in argv


def _reject_missing_print_timeout(argv):
    """Every review-launch, delegate-launch, and setup bind-probe vector
    now carries an explicit `--print-timeout` (mission agy-1111-free-probes:
    `companion.launch._print_timeout_arg` for foreground launches and the
    bind probe, `_BACKGROUND_PRINT_TIMEOUT_ARG` for background ones — see
    that module's docstring for why: agy's own `--print-timeout` defaults
    to `5m0s`, exactly `_AGY_TIMEOUT_SECONDS`, so an unset flag races the
    plugin's own subprocess timeout for the same deadline). An argv
    reaching `_review_launch`, `_delegate_launch`, or `_bind_probe_launch`
    without it is a shape none of those three real companion-built vectors
    would produce anymore — same discipline as
    `_reject_missing_disable_slash_commands`. Loud, not silent.
    Deliberately NOT applied to `_probe_launch`: the free read-only
    slash-command probe vector (`companion/probe.py`, untouched by this
    mission) never carries --print-timeout either — it answers before any
    wait begins, so the flag would be meaningless there."""
    if _carries_print_timeout(argv):
        return True
    sys.stderr.write(
        "fake agy: argv is missing --print-timeout — every review-launch, "
        "delegate-launch, and setup bind-probe vector the real companion "
        "builds carries one; refusing to stand in for one that would never "
        "happen\n"
    )
    return False


def _stdout_for(argv, stream_text, plain_text):
    """`agy` prints an **event stream** ONLY under `--output-format
    stream-json`; a launch without the flag gets plain text. Every branch
    of this fake that synthesizes a stream routes through here so the fake
    can never be more generous than the binary.

    This gate is not cosmetic. Before it existed, the
    `delegate_background_finished` branch emitted a stream unconditionally
    while `delegate._run_background_delegate` never requested one — so
    `status.derive_status` read a real background **delegate** **job**'s
    plain-text `output_file`, found no **result event**, and would have
    reported `running` forever, with the whole suite green.
    `tests/test_log_fidelity.py`'s `FakeAgyStreamJsonGateTest` holds this
    invariant by invoking the fake, not by reading it."""
    return stream_text if _requests_stream_json(argv) else plain_text


def _requested_agent(argv):
    for i, arg in enumerate(argv):
        if arg == "--agent" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _agent_resolves_from_cwd(argv):
    """Model the real binary's agent resolution: a workspace-scoped agent is
    found only at `{cwd}/.agents/agents/{name}/agent.md` (verified — see
    docs/review-schema-verdict.md Finding B). A run whose cwd lacks that file
    falls back to the default agent, silently.

    This is what makes the fake a fake and not a mock: before it existed,
    every review test passed while the shipped code launched agy in a
    directory where the vendored agent could never be found, so `/agy:review`
    silently fell back in real use and no test noticed. Any launch path that
    forgets to stage an **agent workspace** now fails loudly here."""
    name = _requested_agent(argv)
    if name is None:
        return True  # delegate never passes --agent; nothing to resolve.
    return os.path.isfile(os.path.join(os.getcwd(), ".agents", "agents", name, "agent.md"))


def _fallback_log_for(argv):
    name = _requested_agent(argv) or "agy-review"
    return (
        "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
        'W0101 00:00:00.000000       1 printmode.go:166] Agent "{}" not found, '
        "falling back to default\n".format(name)
    )

_BOUND_LOG_TEMPLATE = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    "I0101 00:00:00.000000       1 server.go:934] Created conversation {uuid}\n"
)
_COMPLETION_MARKER_LINE = (
    "I0101 00:00:05.000000       1 conversation_manager.go:666] Stream completed for "
    "{uuid}, clearing ResponsePending\n"
)

# Copied verbatim (modulo timestamp/pid/uuid) from a real agy 1.1.8 log —
# tests/fixtures/delegate/2026-07-30-resume-fallback.log. The binary emits
# BOTH lines for a failed resume, and they differ in a way that matters:
# the klog trace leaves the uuid BARE, the human-facing warning QUOTES it.
# An earlier version of this fake fused them into a single invented line
# with a quoted uuid and a printmode.go prefix, which let
# CONVERSATION_NOT_FOUND_RE ship requiring quotes — matching only the
# presentation text, never the durable trace. Do not "simplify" this back
# into one line; tests/test_log_fidelity.py asserts both against the real
# capture.
_RESUME_FALLBACK_LINES = (
    "I0101 00:00:00.000000       1 server.go:2520] GetConversationDetail: conversation "
    "{uuid} not found locally, searching fallback import dirs\n"
    "W0101 00:00:00.000000       1 common.go:281] Conversation {uuid} not found, "
    "ignoring --conversation flag\n"
    'Warning: conversation "{uuid}" not found.\n'
)

_DEFAULT_REVIEW_JSON = (
    '{"findings": [], "overall_correctness": "patch is correct", '
    '"overall_explanation": "No issues found.", "overall_confidence_score": 0.9}'
)

_STREAM_UUID = "66666666-6666-6666-6666-666666666666"


def _stream_json_wrap(response_text, agent_name, uuid=_STREAM_UUID):
    """Synthesize a minimal **event stream** (NDJSON: one `init` event, one
    `result` event) carrying `response_text` verbatim as the **result
    event**'s `response` field. Models what a real `agy --output-format
    stream-json` run's stdout looks like, closely enough for
    `companion.stream_events.parse_event_stream` to round-trip it — real
    step_update events are not needed here since no test asserts on them,
    only on the response the result event carries."""
    init_event = json.dumps({
        "event": "init",
        "conversation_id": uuid,
        "init": {"cwd": os.getcwd(), "agent": agent_name},
    })
    result_event = json.dumps({
        "event": "result",
        "result": {
            "conversation_id": uuid,
            "status": "SUCCESS",
            "response": response_text,
            "num_turns": 1,
        },
    })
    return init_event + "\n" + result_event + "\n"


def _review_launch(argv):
    """Handle `agy -p <prompt> --disable-slash-commands --agent agy-review
    --sandbox --new-project --print-timeout <T> --log-file <path>` per
    FAKE_AGY_BEHAVIOR."""
    if not _reject_missing_disable_slash_commands(argv):
        return 1
    if not _reject_missing_print_timeout(argv):
        return 1

    log_file = None
    for i, arg in enumerate(argv):
        if arg == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]

    if log_file is None:
        sys.stderr.write("fake agy: missing --log-file\n")
        return 1

    if BEHAVIOR == "review_unknown_bind":
        # Zero evidence either way: no fallback line, no `Created
        # conversation` line, no `printmode.go` line at all — the log the
        # _bind_check tri-state fix must report as "unknown", never "bound"
        # on the strength of stdout looking like a clean review.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write("")
        sys.stdout.write(_DEFAULT_REVIEW_JSON)
        return 0

    if BEHAVIOR == "review_silent_fallback" or not _agent_resolves_from_cwd(argv):
        # Either the test asked for a fallback, or the launch genuinely did
        # not stage an agent workspace — indistinguishable to the caller, and
        # that is the point: the real binary does not distinguish them either.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_fallback_log_for(argv))
        sys.stdout.write("(default agent output, unrelated to the review schema)\n")
        return 0

    if BEHAVIOR == "review_bound_crashed":
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid="11111111-1111-1111-1111-111111111111"))
        sys.stderr.write(
            "E0101 00:00:00.000000       1 printmode.go:272] "
            "Print mode: run ended with error and no response\n"
        )
        return 1

    if BEHAVIOR == "review_background_finished":
        uuid = "33333333-3333-3333-3333-333333333333"
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid=uuid))
            handle.write(_COMPLETION_MARKER_LINE.format(uuid=uuid))
        sys.stdout.write(_stdout_for(
            argv, _stream_json_result(uuid, _DEFAULT_REVIEW_JSON), _DEFAULT_REVIEW_JSON
        ))
        return 0

    if BEHAVIOR == "review_stream_garbage":
        # Bound, but stdout is not a valid **event stream** at all — not
        # even one parseable NDJSON line. The live path must fall back to
        # this raw text rather than raising.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid="77777777-7777-7777-7777-777777777777"))
        sys.stdout.write("not an event stream at all, just garbage text\n")
        return 0

    if BEHAVIOR == "review_stream_no_result":
        # Bound, and stdout is a genuine but truncated **event stream** —
        # an init event and one step update, then nothing — never reaching
        # a **result event**. Models a mid-stream crash.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid="88888888-8888-8888-8888-888888888888"))
        agent_name = _requested_agent(argv) or "agy-review"
        init_event = json.dumps({
            "event": "init",
            "conversation_id": "88888888-8888-8888-8888-888888888888",
            "init": {"cwd": os.getcwd(), "agent": agent_name},
        })
        step_event = json.dumps({
            "event": "step_update",
            "step_update": {"step_index": 0, "step_type": "user_input", "state": "DONE"},
        })
        sys.stdout.write(_stdout_for(
            argv,
            init_event + "\n" + step_event + "\n",
            # Without --output-format stream-json there is no stream to
            # truncate — a run cut off mid-answer just prints the partial
            # plain text it managed before it died.
            '{"findings": [',
        ))
        return 0

    # "review_bound_valid" (also the default for any other/unset BEHAVIOR
    # value, since -p is only ever invoked for the review launch).
    with open(log_file, "w", encoding="utf-8") as handle:
        handle.write(_BOUND_LOG_TEMPLATE.format(uuid="22222222-2222-2222-2222-222222222222"))
    agent_name = _requested_agent(argv) or "agy-review"
    stream_path = os.environ.get("FAKE_AGY_STREAM_PATH")
    if stream_path:
        # Replay a real, already event-stream-shaped capture verbatim —
        # byte-for-byte, no synthetic wrapping — so a test proves the real
        # parse path against real bytes. Only a launch that asked for a
        # stream can receive one; replaying captured stream bytes at a
        # launch that did not is precisely the over-generous fake this
        # module's _stdout_for exists to prevent, so it is a loud error
        # rather than a silent stream.
        if not _requests_stream_json(argv):
            sys.stderr.write(
                "fake agy: FAKE_AGY_STREAM_PATH is set but this launch did not pass "
                "--output-format stream-json\n"
            )
            return 1
        with open(stream_path, "rb") as handle:
            sys.stdout.buffer.write(handle.read())
        return 0
    stdout_path = os.environ.get("FAKE_AGY_STDOUT_PATH")
    if stdout_path:
        with open(stdout_path, "rb") as handle:
            response_text = handle.read().decode("utf-8")
    else:
        response_text = _DEFAULT_REVIEW_JSON
    sys.stdout.write(_stdout_for(
        argv, _stream_json_wrap(response_text, agent_name), response_text
    ))
    return 0

def _is_read_only_probe_vector(argv):
    """True for `-p "/<cmd>" --output-format json [--model M] [--effort E]`
    — see the module docstring's "Read-only slash-command probe behaviors"
    section for why these two properties together (and only together)
    identify this shape uniquely among every `-p` vector this fake
    handles."""
    if len(argv) < 2 or argv[0] != "-p":
        return False
    prompt = argv[1]
    if not (isinstance(prompt, str) and prompt.startswith("/")):
        return False
    return _requests_json_output(argv)


def _requests_json_output(argv):
    for i, arg in enumerate(argv):
        if arg == "--output-format" and i + 1 < len(argv) and argv[i + 1] == "json":
            return True
    return False


def _requested_model_and_effort(argv):
    model = None
    effort = None
    for i, arg in enumerate(argv):
        if arg == "--model" and i + 1 < len(argv):
            model = argv[i + 1]
        if arg == "--effort" and i + 1 < len(argv):
            effort = argv[i + 1]
    return model, effort


PROBE_BEHAVIOR = os.environ.get("FAKE_AGY_PROBE_BEHAVIOR", "probe_default")

_PROBE_DEFAULT_MODEL_ID = "gemini-3.1-pro-high"
_PROBE_DEFAULT_MODEL_LABEL = "Gemini 3.1 Pro (High)"
_PROBE_DEFAULT_EFFORT = "high"

# "probe_mismatch"'s two fixed answers: one for a bare probe, a DIFFERENT
# one for a probe that forwarded --model/--effort — see the module
# docstring. Never equal to each other; the whole point is a comparison
# that must fail.
_PROBE_MISMATCH_BASELINE_MODEL = "gemini-probe-baseline-model"
_PROBE_MISMATCH_BASELINE_EFFORT = "medium"
_PROBE_MISMATCH_FALLBACK_MODEL = "gemini-probe-fallback-model"
_PROBE_MISMATCH_FALLBACK_EFFORT = "low"

# "probe_ignores_model"'s one fixed answer, returned NO MATTER what (if
# anything) was forwarded — see its dispatch branch above for why this is
# a different, more dangerous shape than "probe_mismatch".
_PROBE_IGNORES_MODEL_ID = "gemini-persisted-default"
_PROBE_IGNORES_MODEL_LABEL = "Gemini Persisted Default"
_PROBE_IGNORES_MODEL_EFFORT = "high"

# Copied from a real agy 1.1.11 `/usage --output-format json` capture
# (2026-08-07) — see companion/probe.py's module docstring for the full
# envelope. Kept verbatim rather than reshaped for "tidiness": the
# gemini-5h/3p-weekly/3p-5h buckets genuinely carry no "description" key in
# the real response, only gemini-weekly does. This is the fixture that
# proves the OPTIONAL-description handling, not an invented edge case.
_PROBE_USAGE_DATA = {
    "description": "Within each group, models share a weekly limit and a "
    "5-hour limit. ...",
    "groups": [
        {
            "name": "Gemini Models",
            "description": "Models within this group: Gemini Flash, Gemini Pro",
            "buckets": [
                {
                    "id": "gemini-weekly",
                    "name": "Weekly Limit Remaining",
                    "description": "You have used some of your weekly limit, "
                    "it will fully refresh in 6 days, 22 hours.",
                    "window": "weekly",
                    "remaining_fraction": 0.997,
                    "reset_time": "2026-08-14T07:11:25Z",
                },
                {
                    "id": "gemini-5h",
                    "name": "Five Hour Limit Remaining",
                    "window": "5h",
                    "remaining_fraction": 0.982,
                    "reset_time": "2026-08-07T12:11:25Z",
                },
            ],
        },
        {
            "name": "Claude and GPT models",
            "description": "Models within this group: Claude Opus, Claude "
            "Sonnet, GPT-OSS",
            "buckets": [
                {
                    "id": "3p-weekly",
                    "name": "Weekly Limit Remaining",
                    "window": "weekly",
                    "remaining_fraction": 1,
                    "reset_time": "2026-08-14T08:33:51Z",
                },
                {
                    "id": "3p-5h",
                    "name": "Five Hour Limit Remaining",
                    "window": "5h",
                    "remaining_fraction": 1,
                    "reset_time": "2026-08-07T13:33:51Z",
                },
            ],
        },
    ],
}


def _probe_envelope(command, data):
    """The real 1.1.11 envelope shape (verified capture, reproduced in
    companion/probe.py's module docstring): empty `conversation_id`,
    `num_turns: 0`, all-zero `usage` — the proof this path is free — plus
    the typed `command.data` payload callers actually read."""
    return json.dumps({
        "conversation_id": "",
        "status": "SUCCESS",
        "response": "",
        "duration_seconds": 0,
        "num_turns": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
        },
        "command": {"name": command, "data": data},
    })


def _probe_launch(argv):
    """Handle the free 1.1.11 read-only slash-command probe. Deliberately
    does NOT call `_reject_missing_disable_slash_commands` — this is the
    one `-p` vector this fake accepts without that flag, precisely because
    it is the one vector the real companion deliberately builds without it
    (companion/probe.py's module docstring). `argv` here is everything
    after `-p` — `argv[0]` is the prompt, e.g. "/model"."""
    command = argv[0][1:]
    model, effort = _requested_model_and_effort(argv)

    if PROBE_BEHAVIOR == "probe_error":
        sys.stderr.write("Error: fake agy probe failure\n")
        return 1

    if PROBE_BEHAVIOR == "probe_garbage_json":
        sys.stdout.write("not json at all\n")
        return 0

    if PROBE_BEHAVIOR == "probe_missing_command":
        sys.stdout.write(json.dumps({
            "conversation_id": "",
            "status": "SUCCESS",
            "response": "",
            "duration_seconds": 0,
            "num_turns": 0,
            "usage": {
                "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
                "cache_read_tokens": 0, "total_tokens": 0,
            },
        }))
        return 0

    if PROBE_BEHAVIOR == "probe_wrong_name":
        sys.stdout.write(_probe_envelope("not-" + command, {}))
        return 0

    if PROBE_BEHAVIOR == "probe_ignores_model" and command == "model":
        # The regression this behavior exists to catch: a binary that
        # accepts --model but never actually applies it, always answering
        # with whatever is already persisted — REGARDLESS of what (if
        # anything) was forwarded, unlike "probe_mismatch" below which
        # answers differently depending on whether --model was present.
        # This is precisely the shape that fooled the first version of
        # setup._probe_model_forwarding: that version forwarded the SAME
        # value it had just read as the baseline, so "ignored" and
        # "honored" produced byte-identical answers here and the check
        # could not tell them apart. The fixed version forwards a value
        # that DIFFERS from the baseline, so against THIS behavior it must
        # now report "mismatch", never "confirmed".
        data = {
            "id": _PROBE_IGNORES_MODEL_ID,
            "label": _PROBE_IGNORES_MODEL_LABEL,
            "effort": _PROBE_IGNORES_MODEL_EFFORT,
            "is_default": True,
        }
        sys.stdout.write(_probe_envelope("model", data))
        return 0

    if PROBE_BEHAVIOR == "probe_mismatch" and command == "model":
        if model is None:
            data = {
                "id": _PROBE_MISMATCH_BASELINE_MODEL,
                "label": "Probe Baseline Model",
                "effort": _PROBE_MISMATCH_BASELINE_EFFORT,
                "is_default": True,
            }
        else:
            # A silent fallback: forwarding was requested, but the answer
            # ignores it and reports a DIFFERENT model/effort — exactly
            # the defect setup._probe_model_forwarding exists to catch.
            data = {
                "id": _PROBE_MISMATCH_FALLBACK_MODEL,
                "label": "Probe Fallback Model",
                "effort": _PROBE_MISMATCH_FALLBACK_EFFORT,
                "is_default": False,
            }
        sys.stdout.write(_probe_envelope("model", data))
        return 0

    # "probe_default" (also the default for any other/unset value): a
    # well-behaved agy whose answer genuinely reflects whatever
    # --model/--effort was forwarded.
    if command == "model":
        data = {
            "id": model or _PROBE_DEFAULT_MODEL_ID,
            "label": _PROBE_DEFAULT_MODEL_LABEL,
            "effort": effort or _PROBE_DEFAULT_EFFORT,
            "is_default": model is None,
        }
    elif command == "usage":
        data = _PROBE_USAGE_DATA
    elif command == "credits":
        data = {"remaining_credits": 0}
    elif command == "effort":
        data = {"effort": effort or _PROBE_DEFAULT_EFFORT}
    elif command == "skills":
        data = {"skills": []}
    else:
        data = {}
    sys.stdout.write(_probe_envelope(command, data))
    return 0


BIND_PROBE_BEHAVIOR = os.environ.get("FAKE_AGY_BIND_PROBE_BEHAVIOR", "bind_probe_no_fallback")

_BIND_PROBE_RESOLVED_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    "I0101 00:00:00.000000       1 printmode.go:154] agent resolved\n"
    "I0101 00:00:00.000000       1 printmode.go:330] validating model\n"
    "I0101 00:00:00.000000       1 printmode.go:332] model validation failed\n"
)


def _bind_probe_launch(argv):
    """Handle the setup doctor's zero-quota bind probe: `agy -p <prompt>
    --disable-slash-commands --agent agy-review --model <invalid> --sandbox
    --new-project --log-file <path> --print-timeout <T>` (see
    docs/review-schema-verdict.md Finding A). Dispatches on
    FAKE_AGY_BIND_PROBE_BEHAVIOR, never FAKE_AGY_BEHAVIOR."""
    if not _reject_missing_disable_slash_commands(argv):
        return 1
    if not _reject_missing_print_timeout(argv):
        return 1

    log_file = None
    for i, arg in enumerate(argv):
        if arg == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]

    if log_file is None:
        sys.stderr.write("fake agy: missing --log-file\n")
        return 1

    if BIND_PROBE_BEHAVIOR == "bind_probe_unknown":
        # An empty --log-file: no fallback trace, no printmode.go trace, no
        # Created conversation line — the doctor must report "unknown", the
        # same zero-evidence shape review_unknown_bind exercises on the
        # foreground live path.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write("")
        sys.stderr.write("Error: invalid model selection\n")
        return 1

    if BIND_PROBE_BEHAVIOR == "bind_probe_not_bound" or not _agent_resolves_from_cwd(argv):
        # Same rule as the review launch: a probe run from a directory with no
        # staged agent must report NOT BOUND, which is exactly the real-world
        # failure /agy:setup exists to catch.
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_fallback_log_for(argv))
        sys.stderr.write("Error: invalid model selection\n")
        return 1

    # "bind_probe_no_fallback" (default): agent resolution reached, no
    # fallback line, but --model is invalid so agy exits before any model
    # call, per Finding A — no `Created conversation` line is ever written
    # on this path, so this is genuinely "unknown", not "bound".
    with open(log_file, "w", encoding="utf-8") as handle:
        handle.write(_BIND_PROBE_RESOLVED_LOG)
    sys.stderr.write("Error: invalid model selection\n")
    return 1


_DELEGATE_STDOUT = "agy delegate output: task complete.\n"
_DELEGATE_FRESH_UUID = "44444444-4444-4444-4444-444444444444"
_DELEGATE_FALLBACK_NEW_UUID = "55555555-5555-5555-5555-555555555555"


def _event_stream_stdout(conversation_id, response=_DELEGATE_STDOUT):
    """A minimal init+result **event stream** (NDJSON), field names matching
    tests/fixtures/stream_events/2026-08-02-run1.ndjson's real shape —
    only the fields M3's bind check and response-forwarding actually read."""
    init_event = json.dumps({
        "event": "init",
        "conversation_id": conversation_id,
        "init": {},
    })
    result_event = json.dumps({
        "event": "result",
        "result": {
            "conversation_id": conversation_id,
            "status": "SUCCESS",
            "response": response,
            "num_turns": 1,
            "duration_seconds": 0.1,
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "thinking_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 2,
            },
        },
    })
    return init_event + "\n" + result_event + "\n"


def _delegate_launch(argv):
    """Handle `agy -p <task> --disable-slash-commands
    --dangerously-skip-permissions --sandbox (--new-project |
    --conversation <uuid>) --log-file <path> --print-timeout <T>
    [--model M] [--effort E]` per a `delegate_*` FAKE_AGY_BEHAVIOR value."""
    if not _reject_missing_disable_slash_commands(argv):
        return 1
    if not _reject_missing_print_timeout(argv):
        return 1

    log_file = None
    requested_conversation = None
    for i, arg in enumerate(argv):
        if arg == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]
        if arg == "--conversation" and i + 1 < len(argv):
            requested_conversation = argv[i + 1]

    if log_file is None:
        sys.stderr.write("fake agy: missing --log-file\n")
        return 1

    if BEHAVIOR == "delegate_resume_silent_fallback":
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_RESUME_FALLBACK_LINES.format(uuid=requested_conversation))
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid=_DELEGATE_FALLBACK_NEW_UUID))
        # Clean exit 0, ordinary-looking stdout, empty stderr — the silent
        # fallback signature: no signal outside the log that this did not
        # resume the requested conversation. Under --output-format
        # stream-json, the event stream itself reports SUCCESS on the WRONG
        # conversation_id — see module docstring.
        if _requests_stream_json(argv):
            sys.stdout.write(_event_stream_stdout(_DELEGATE_FALLBACK_NEW_UUID))
        else:
            sys.stdout.write(_DELEGATE_STDOUT)
        return 0

    if BEHAVIOR == "delegate_resume_bound":
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(
                "I0101 00:00:00.000000       1 printmode.go:108] resolving conversation\n"
            )
            handle.write(_COMPLETION_MARKER_LINE.format(uuid=requested_conversation))
        if _requests_stream_json(argv):
            sys.stdout.write(_event_stream_stdout(requested_conversation))
        else:
            sys.stdout.write(_DELEGATE_STDOUT)
        return 0

    if BEHAVIOR == "delegate_background_finished":
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(_BOUND_LOG_TEMPLATE.format(uuid=_DELEGATE_FRESH_UUID))
            handle.write(_COMPLETION_MARKER_LINE.format(uuid=_DELEGATE_FRESH_UUID))
        sys.stdout.write(_stdout_for(
            argv,
            _stream_json_result(_DELEGATE_FRESH_UUID, _DELEGATE_STDOUT),
            _DELEGATE_STDOUT,
        ))
        return 0

    # "delegate_fresh_bound" (also the default for any other/unset
    # delegate_* value).
    with open(log_file, "w", encoding="utf-8") as handle:
        handle.write(_BOUND_LOG_TEMPLATE.format(uuid=_DELEGATE_FRESH_UUID))
    sys.stdout.write(_DELEGATE_STDOUT)
    return 0


# FAKE_AGY_VERSION lets a test pin the version string `agy
# --version` reports, so the doctor's version-floor check
# (setup.MIN_AGY_VERSION) can be exercised on both sides — a supported
# version and a below-floor one — without ever invoking the real binary.
# Defaults to "1.1.6", below the floor, so a test that does not care about
# the version floor exercises the below-floor branch by default.
VERSION_NUMBER = os.environ.get("FAKE_AGY_VERSION", "1.1.6")
VERSION_BLOCK = (
    "\n"
    "  Antigravity CLI\n"
    "\n"
    "  CLI version:  {}\n"
    "\n"
).format(VERSION_NUMBER)

AGENTS_WITH_ONE = "Available agents:\n  code-auditor\n"
AGENTS_EMPTY = "Available agents:\n"

NOT_AUTHENTICATED_STDERR = (
    "No valid authentication found (). Starting login...\n"
    "Warning: could not determine authenticated account: ... "
    "You are not logged into Antigravity.\n"
)
OTHER_FAILURE_STDERR = "Error: could not reach the agent registry\n"

# `agy models` — a real, separate free subcommand (not a `-p` invocation at
# all), used by setup._probe_model_forwarding to choose a probe target
# guaranteed to differ from the baseline `/model` answer. Dispatches on
# FAKE_AGY_MODELS_BEHAVIOR, independent of every other FAKE_AGY_* var:
#
#   - "models_ok" (default): the real 1.1.11 shape verified live
#     2026-08-07 — a "Fetching available models..." header line, then
#     tab-separated `id\tlabel` lines, including some ids with no effort
#     suffix at all (claude-sonnet-4-6, gpt-oss-120b-medium) — real shapes,
#     not invented for symmetry.
#   - "models_fails": exits 1 — models "unknown", never a guess.
#   - "models_garbage": exits 0 but no line has a tab — every line is
#     unparseable, so `_parse_models` recovers zero models — "unknown".
#   - "models_too_few": exits 0 with exactly one parseable model line —
#     `setup._probe_model_forwarding` must treat "< 2 distinct ids" as
#     "unknown", never as "nothing to compare against so call it
#     confirmed".
MODELS_BEHAVIOR = os.environ.get("FAKE_AGY_MODELS_BEHAVIOR", "models_ok")

_MODELS_LIST_STDOUT = (
    "Fetching available models...\n"
    "gemini-3.6-flash-high\tGemini 3.6 Flash (High)\n"
    "gemini-3.6-flash-medium\tGemini 3.6 Flash (Medium)\n"
    "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)\n"
    "gemini-3.5-flash-high\tGemini 3.5 Flash (High)\n"
    "gemini-3.5-flash-medium\tGemini 3.5 Flash (Medium)\n"
    "gemini-3.5-flash-low\tGemini 3.5 Flash (Low)\n"
    "gemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
    "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
    "claude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n"
    "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
)


def _models_launch(argv):
    if MODELS_BEHAVIOR == "models_fails":
        sys.stderr.write("Error: could not fetch available models\n")
        return 1
    if MODELS_BEHAVIOR == "models_garbage":
        sys.stdout.write("Fetching available models...\nsomething went wrong, no tabs here\n")
        return 0
    if MODELS_BEHAVIOR == "models_too_few":
        sys.stdout.write(
            "Fetching available models...\ngemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
        )
        return 0
    sys.stdout.write(_MODELS_LIST_STDOUT)
    return 0


def main(argv):
    if not argv:
        sys.stderr.write("fake agy: missing subcommand\n")
        return 1

    if argv[0] == "--version":
        sys.stdout.write(VERSION_BLOCK)
        return 0

    if argv[0] == "-p":
        # Checked FIRST: companion.probe's forwarding probe can also carry
        # --model/--effort (that is the whole point of the model-forwarding
        # proof), so this shape must be recognized before the `--model`
        # check below or a forwarding probe would be misrouted into the
        # bind probe, which expects a "ping" prompt and an intentionally
        # invalid model. See the module docstring's "Read-only slash-command
        # probe behaviors" section.
        if _is_read_only_probe_vector(argv):
            return _probe_launch(argv[1:])
        if "--model" in argv:
            return _bind_probe_launch(argv[1:])
        if BEHAVIOR.startswith("delegate_"):
            return _delegate_launch(argv[1:])
        return _review_launch(argv[1:])

    if argv[0] == "models":
        return _models_launch(argv[1:])

    if argv[0] == "agents":
        if BEHAVIOR == "authenticated_with_agents":
            sys.stdout.write(AGENTS_WITH_ONE)
            return 0
        if BEHAVIOR == "authenticated_no_agents":
            sys.stdout.write(AGENTS_EMPTY)
            return 0
        if BEHAVIOR == "not_authenticated":
            sys.stderr.write(NOT_AUTHENTICATED_STDERR)
            return 1
        if BEHAVIOR == "agents_list_fails":
            sys.stderr.write(OTHER_FAILURE_STDERR)
            return 1
        sys.stderr.write("fake agy: unknown FAKE_AGY_BEHAVIOR {}\n".format(BEHAVIOR))
        return 1

    sys.stderr.write("fake agy: unknown subcommand {}\n".format(argv[0]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
