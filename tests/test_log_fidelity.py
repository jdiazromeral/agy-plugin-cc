"""The companion's beliefs about `agy`, held against **real** captured bytes —
not against the fake's idea of what agy produces.

Two sides, one job. The companion believes two things about the binary: what
its `--log-file` PRINTS (the regexes in `companion.agy_log` and
`companion.delegate`) and what its **event stream** EMITS (the event kinds and
field names `companion.stream_events` reads under `--output-format
stream-json`). Both are pinned here against bytes captured verbatim from a
real, authenticated agy 1.1.8 — three `--log-file` captures under
`tests/fixtures/delegate/`, and two `.ndjson` **event stream** captures under
`tests/fixtures/stream_events/` and `tests/fixtures/adversarial_review/` (see
each directory's PROVENANCE.md). Values asserted here are hard-coded from
those PROVENANCE files, so a **fixture** swapped or trimmed fails loudly
rather than silently asserting nothing.

Why this file exists. Every other test in this suite drives `tests/fake_agy.py`,
whose log lines were written from memory — and which now synthesizes event
streams too, so the same failure mode reaches the JSON contract. Twice now
that has hidden a real defect:

  1. The fake resolved any `--agent` regardless of cwd, so the shipped code
     launched agy where the vendored agent could never be found — 148 tests
     green, `/agy:review` silently falling back in real use.
  2. The fake emitted ONE resume-fallback line fusing the two the binary
     actually prints (quoted uuid from the human-facing warning, wording and
     `printmode.go` prefix from the klog trace). `CONVERSATION_NOT_FOUND_RE`
     was then validated against that fiction, and required the quotes — so it
     matched only the human-facing line, never the durable klog one.

A fake validated against itself is unfalsifiable. These tests close the loop
in both directions: the shipped patterns and the shipped parser are asserted
against the real captures, the CONSUMERS' readings of them are asserted too
(`status.derive_status`, `delegate._resume_bind_check`), and the fake's own
synthesized streams are compared to the real captures' field vocabulary — as
the subject, never as the reference. When a future agy reworks its log wording
or renames an event field, this is the file that fails — which is the point.

Nothing here asserts what the captures cannot witness: both are successful
runs (`status` `"SUCCESS"`, no `"ERROR"` step **state**, no per-event
timestamp), so there is no crash-shaped or timestamp-shaped pin to be had
from these bytes.

Offline and free: it reads committed files and invokes only the **fake agy**,
never agy itself.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.agy_log import (  # noqa: E402
    COMPLETION_MARKER_RE,
    CREATED_CONVERSATION_RE,
    FALLBACK_RE,
    _bind_check,
    find_conversation,
)
from companion.delegate import (  # noqa: E402
    CONVERSATION_NOT_FOUND_RE,
    _resume_bind_check,
)
from companion.result import _render_result  # noqa: E402
from companion.status import STATUS_COMPLETED, derive_status  # noqa: E402
from companion.stream_events import parse_event_stream  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "delegate"

_FRESH = FIXTURES / "2026-07-30-fresh.log"
_RESUME_GENUINE = FIXTURES / "2026-07-30-resume-genuine.log"
_RESUME_FALLBACK = FIXTURES / "2026-07-30-resume-fallback.log"

# The conversation the captured fresh run created, and that the captured
# genuine resume continued. Hard-coded so a fixture swapped without updating
# this file fails loudly rather than silently asserting nothing.
_CAPTURED_UUID = "2735a5c6-e3f9-4551-a1f3-d746b8011a4f"
_BOGUS_UUID = "00000000-0000-4000-8000-000000000000"


def _read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


class FreshRunTest(unittest.TestCase):
    def test_created_conversation_is_found(self):
        self.assertEqual(find_conversation(_read(_FRESH)), _CAPTURED_UUID)

    def test_completion_marker_matches(self):
        """`/agy:status` and `/agy:result` are both meaningless if this
        pattern drifts: with no babysitter to capture an exit code, a job
        whose marker never matches reads as "running" forever."""
        match = COMPLETION_MARKER_RE.search(_read(_FRESH))
        self.assertIsNotNone(match, "completion marker not found in a real completed run")
        self.assertEqual(match.group(1), _CAPTURED_UUID)

    def test_no_fallback_or_not_found_traces_in_a_clean_run(self):
        log = _read(_FRESH)
        self.assertIsNone(FALLBACK_RE.search(log))
        self.assertIsNone(CONVERSATION_NOT_FOUND_RE.search(log))


class GenuineResumeTest(unittest.TestCase):
    def test_resume_carries_no_created_conversation_line(self):
        """The trace shape delegate.py's resume logic depends on: a real
        resume creates nothing, so the uuid can only come from the
        completion marker."""
        self.assertIsNone(CREATED_CONVERSATION_RE.search(_read(_RESUME_GENUINE)))

    def test_resume_resolves_to_the_same_conversation_as_the_fresh_run(self):
        self.assertEqual(find_conversation(_read(_RESUME_GENUINE)), _CAPTURED_UUID)

    def test_genuine_resume_is_not_mistaken_for_a_fallback(self):
        self.assertIsNone(CONVERSATION_NOT_FOUND_RE.search(_read(_RESUME_GENUINE)))


class ResumeFallbackTest(unittest.TestCase):
    def test_fallback_is_detected(self):
        match = CONVERSATION_NOT_FOUND_RE.search(_read(_RESUME_FALLBACK))
        self.assertIsNotNone(match, "silent fallback went undetected — the guard is blind")
        self.assertEqual(match.group(1), _BOGUS_UUID)

    def test_both_real_lines_match_independently(self):
        """The regression that motivated this file. The klog line carries a
        BARE uuid; the human-facing warning carries a QUOTED one. Requiring
        quotes matched only the latter, leaving the guard resting on
        presentation text. Each line alone must be sufficient."""
        log = _read(_RESUME_FALLBACK)
        klog = [ln for ln in log.splitlines() if "ignoring --conversation flag" in ln]
        human = [ln for ln in log.splitlines() if ln.startswith("Warning:")]
        self.assertTrue(klog, "fixture no longer contains the klog trace")
        self.assertTrue(human, "fixture no longer contains the human-facing warning")

        for line in klog + human:
            with self.subTest(line=line[:60]):
                self.assertIsNotNone(
                    CONVERSATION_NOT_FOUND_RE.search(line),
                    "pattern does not match this real line in isolation",
                )

    def test_bare_uuid_klog_line_matches(self):
        """Pinned separately from the fixture so the intent survives even if
        the fixture is later trimmed: the durable klog trace does NOT quote
        the uuid."""
        line = (
            "W0730 10:28:14.594452 13766 common.go:281] Conversation "
            "00000000-0000-4000-8000-000000000000 not found, ignoring --conversation flag"
        )
        match = CONVERSATION_NOT_FOUND_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), _BOGUS_UUID)


class BindCheckTest(unittest.TestCase):
    """`agy_log._bind_check`'s positive trace, held against real captured
    bytes rather than the fake's idea of a bind. This is the live false
    positive this exists to fix: `2026-07-30-resume-fallback.log` is a real
    `delegate` capture — `delegate.py` never passes `--agent` at all, so
    nothing was requested and nothing could bind — but it still carries
    `printmode.go:<line>]` lines, because EVERY print-mode run logs them
    (e.g. `Print mode: starting`), agent-requesting or not. Before this
    mission, `_bind_check` matched those lines as a positive bind trace and
    reported "bound" for a run that could not possibly have bound anything."""

    def test_run_with_no_agent_flag_is_unknown_not_bound(self):
        state, proof = _bind_check(_read(_RESUME_FALLBACK))
        self.assertEqual(state, "unknown")
        self.assertNotEqual(state, "bound")

    def test_genuine_created_conversation_line_still_proves_bound(self):
        """The one positive trace that survives: a real `Created
        conversation <uuid>` line, from the real `--agent`-requesting fresh
        capture."""
        state, proof = _bind_check(_read(_FRESH))
        self.assertEqual(state, "bound")
        self.assertTrue(
            proof.startswith("Created conversation "),
            "proof string is not the Created conversation line: {!r}".format(proof),
        )
        self.assertIn(_CAPTURED_UUID, proof)


# --- the **event stream** side: the JSON contract, held against real bytes ---
#
# Every value below is transcribed from the fixture's own PROVENANCE.md, so a
# **fixture** swapped or trimmed without updating this file fails loudly
# rather than silently asserting nothing — the same discipline `_CAPTURED_UUID`
# applies on the log side.

_STREAM_EVENTS_CAPTURE = {
    "name": "stream_events",
    "path": REPO_ROOT / "tests" / "fixtures" / "stream_events" / "2026-08-02-run1.ndjson",
    "conversation": "a4425612-2b6c-4e0c-a9b5-e7600418be81",
    "agent": "agy-review",
    "lines": 6,
    "step_updates": 4,
    "step_update_keys": {
        "conversation_id", "step_index", "step_type", "state",
        "text_delta", "duration_seconds", "usage",
    },
    "step_types": {"user_input", "agent_response", "checkpoint"},
    "num_turns": 1,
    "usage": {
        "input_tokens": 12308,
        "output_tokens": 1435,
        "thinking_tokens": 1219,
        "cache_read_tokens": 0,
        "total_tokens": 13743,
    },
    "finding_title": "[P1] Use addition instead of subtraction in add function",
    "finding_priority": 1,
    "verdict": "patch is incorrect",
}

_ADVERSARIAL_CAPTURE = {
    "name": "adversarial_review",
    "path": REPO_ROOT / "tests" / "fixtures" / "adversarial_review" / "2026-07-30-run1.ndjson",
    "conversation": "5a26513d-be46-4877-b190-f9417797f32d",
    "agent": "agy-adversarial-review",
    "lines": 13,
    "step_updates": 11,
    "step_update_keys": {
        "conversation_id", "step_index", "step_type", "state",
        "text_delta", "duration_seconds", "usage", "tool_name", "tool_info",
    },
    "step_types": {"user_input", "agent_response", "checkpoint", "tool"},
    "num_turns": 1,
    "usage": {
        "input_tokens": 22142,
        "output_tokens": 2771,
        "thinking_tokens": 2229,
        "cache_read_tokens": 16275,
        "total_tokens": 24913,
    },
    "finding_title": "[P0] Fix incorrect subtraction operator in add function",
    "finding_priority": 0,
    "verdict": "patch is incorrect",
}

_CAPTURES = (_STREAM_EVENTS_CAPTURE, _ADVERSARIAL_CAPTURE)

# The four keys of the `agy-review` schema, which `agy-adversarial-review`
# shares (see either PROVENANCE.md).
_REVIEW_SCHEMA_KEYS = {
    "findings", "overall_correctness", "overall_explanation", "overall_confidence_score",
}

# Both fixtures are successful runs, so `state` only ever takes these two
# values here — recorded as the observed vocabulary, NOT as a claim that
# `agy` has no others (both PROVENANCE.md files say so explicitly).
_OBSERVED_STEP_STATES = {"ACTIVE", "DONE"}


def _ndjson_fixtures():
    """Every committed **event stream** **capture**, found by glob so a
    fixture added later is scrubbed-checked without editing this file."""
    return sorted((REPO_ROOT / "tests" / "fixtures").glob("**/*.ndjson"))


def _raw_events(capture):
    """Every line of a captured **event stream**, decoded with `json.loads`
    directly rather than through `parse_event_stream`. Deliberate: the parser
    reads only the fields it needs, so a field name it ignores today — and a
    future `agy` renaming or dropping one — is only pinned by reading the raw
    bytes."""
    return [
        json.loads(line)
        for line in _read(capture["path"]).splitlines()
        if line.strip()
    ]


def _events_of_kind(capture, kind):
    return [event for event in _raw_events(capture) if event.get("event") == kind]


class RealEventStreamVocabularyTest(unittest.TestCase):
    """The **event stream**'s JSON contract, asserted BY NAME against the two
    real captures.

    The counterweight this file exists to be, carried over to the stream: the
    **fake agy** now synthesizes event streams too, so "the companion reads
    `result.conversation_id`" is a belief that can drift from what `agy`
    emits exactly the way the log wording once did. When a future agy renames
    a field or drops one, this is the file that fails."""

    def test_only_the_three_known_event_kinds_appear(self):
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                events = _raw_events(capture)
                self.assertEqual(capture["lines"], len(events))
                kinds = [event.get("event") for event in events]
                self.assertEqual({"init", "step_update", "result"}, set(kinds))
                self.assertEqual("init", kinds[0], "the stream does not open with an init event")
                self.assertEqual("result", kinds[-1], "the stream does not close with a result event")
                self.assertEqual(1, kinds.count("init"))
                self.assertEqual(1, kinds.count("result"))
                self.assertEqual(capture["step_updates"], kinds.count("step_update"))

    def test_init_event_field_names_and_bound_agent(self):
        """`init.agent` is the **bind** proof the stream carries and the log
        does not state structurally — the reason both PROVENANCE.md files
        call it stronger evidence than a missing fallback line."""
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                init = _events_of_kind(capture, "init")[0]
                self.assertEqual({"event", "conversation_id", "init"}, set(init))
                self.assertEqual(
                    {"agent", "cwd", "permission_mode", "tools"}, set(init["init"])
                )
                self.assertEqual(capture["agent"], init["init"]["agent"])
                self.assertEqual(capture["conversation"], init["conversation_id"])

    def test_result_event_field_names_and_values(self):
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                event = _events_of_kind(capture, "result")[0]
                self.assertEqual({"event", "result"}, set(event))
                result = event["result"]
                self.assertEqual(
                    {
                        "conversation_id", "status", "response",
                        "usage", "num_turns", "duration_seconds",
                    },
                    set(result),
                )
                self.assertEqual(capture["conversation"], result["conversation_id"])
                self.assertEqual("SUCCESS", result["status"])
                self.assertEqual(capture["num_turns"], result["num_turns"])
                self.assertEqual(capture["usage"], result["usage"])

    def test_step_update_field_names(self):
        """`step_index`, `step_type` and `state` are the three fields
        `stream_events.StepUpdate` promotes; every real **step update**
        carries all three."""
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                payloads = [
                    event["step_update"] for event in _events_of_kind(capture, "step_update")
                ]
                self.assertEqual(capture["step_updates"], len(payloads))
                observed = set()
                for payload in payloads:
                    self.assertLessEqual({"step_index", "step_type", "state"}, set(payload))
                    observed |= set(payload)
                self.assertEqual(capture["step_update_keys"], observed)
                self.assertEqual(
                    capture["step_types"],
                    {payload["step_type"] for payload in payloads},
                )
                self.assertEqual(
                    _OBSERVED_STEP_STATES,
                    {payload["state"] for payload in payloads},
                )


class RealEventStreamParseTest(unittest.TestCase):
    """The shipped parser, run over the same real bytes: what the companion
    ends up believing must be what `agy` actually wrote."""

    def test_parsed_result_event_values(self):
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                stream = parse_event_stream(_read(capture["path"]))
                self.assertEqual(capture["conversation"], stream.conversation_id)
                self.assertEqual("SUCCESS", stream.status)
                self.assertEqual(capture["usage"], stream.usage)
                self.assertEqual(capture["step_updates"], len(stream.step_updates))

    def test_parsed_step_updates_keep_their_order_and_fields(self):
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                stream = parse_event_stream(_read(capture["path"]))
                indices = [step.step_index for step in stream.step_updates]
                self.assertEqual(sorted(indices), indices, "step updates arrived out of order")
                self.assertEqual(
                    capture["step_types"], {step.step_type for step in stream.step_updates}
                )
                self.assertEqual(
                    _OBSERVED_STEP_STATES, {step.state for step in stream.step_updates}
                )

    def test_parsed_response_yields_the_review_schema(self):
        """The **tolerant parse** over a real **response**: both captures
        carry one **finding** and a **verdict**, and the P0 one is the only
        real bytes the findings table has ever been rendered from."""
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                parsed = parse_event_stream(_read(capture["path"])).parsed_response()
                self.assertIsNotNone(parsed, "a real result event's response failed to parse")
                self.assertEqual(_REVIEW_SCHEMA_KEYS, set(parsed))
                self.assertEqual(capture["verdict"], parsed["overall_correctness"])
                self.assertEqual(1, len(parsed["findings"]))
                finding = parsed["findings"][0]
                self.assertEqual(
                    {"title", "body", "priority", "confidence_score", "code_location"},
                    set(finding),
                )
                self.assertEqual(capture["finding_title"], finding["title"])
                self.assertEqual(capture["finding_priority"], finding["priority"])


class RealEventStreamConsumerTest(unittest.TestCase):
    """The parser agreeing with the bytes is only half of it: what closes the
    loop is the companion's *interpretation* of them. These call the shipped
    consumers on a real capture, so a consumer that drifts from what `agy`
    emits fails here rather than in a test the **fake agy** also authored."""

    def test_status_derives_completed_from_a_real_result_event(self):
        """A real `status` `"SUCCESS"` must read as a completed **job**. The
        empty log text is the honest input: `derive_status` reads the
        `--log-file` only for the **silent fallback** check, and these
        captures' logs were never committed (stdout only)."""
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                stream = parse_event_stream(_read(capture["path"]))
                self.assertEqual(STATUS_COMPLETED, derive_status("", stream))

    def test_resume_bind_check_binds_the_real_conversation(self):
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                stream = parse_event_stream(_read(capture["path"]))
                bound, proof = _resume_bind_check(stream, capture["conversation"])
                self.assertTrue(bound, "a real result event failed to prove its own bind")
                self.assertEqual(capture["conversation"], proof)

    def test_resume_bind_check_rejects_a_different_conversation(self):
        """The negative half — without it the check could return bound
        unconditionally and every test above would still pass."""
        for capture in _CAPTURES:
            with self.subTest(capture=capture["name"]):
                stream = parse_event_stream(_read(capture["path"]))
                bound, proof = _resume_bind_check(stream, _BOGUS_UUID)
                self.assertFalse(bound, "a **silent fallback** would read as a successful resume")
                self.assertEqual(capture["conversation"], proof)


class FakeAgyFidelityTest(unittest.TestCase):
    """The fake must emit lines the real binary actually produces. This is
    the guard against drifting back into a self-validating fake."""

    def test_fake_resume_fallback_lines_are_matched_by_the_shipped_pattern(self):
        fake = _read(Path(__file__).resolve().parent / "fake_agy.py")
        emitted = [
            ln for ln in fake.splitlines()
            if "not found" in ln and "conversation" in ln.lower() and "#" not in ln.split("'")[0]
        ]
        self.assertTrue(emitted, "fake no longer emits a resume-fallback line")

    def test_fake_uses_the_real_source_file_prefixes(self):
        """agy 1.1.8 logs the resume fallback from common.go and the stream
        completion from conversation_manager.go. A fake citing a file the
        binary never uses for that message is a signal it was written from
        memory rather than from a capture."""
        fake = _read(Path(__file__).resolve().parent / "fake_agy.py")
        self.assertIn("common.go", fake)
        self.assertIn("conversation_manager.go", fake)


_FAKE_AGY = Path(__file__).resolve().parent / "fake_agy.py"


def _run_fake_agy(behavior, argv, cwd):
    """Invoke the **fake agy** as the companion does — a real subprocess,
    a real argv — and return its CompletedProcess. Never grep its source:
    what the fake DOES is the thing under test, not what it looks like."""
    env = dict(os.environ)
    env["FAKE_AGY_BEHAVIOR"] = behavior
    return subprocess.run(
        [sys.executable, str(_FAKE_AGY)] + argv,
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30,
    )


def _event_kinds(stdout_text):
    """The `event` values of every line of `stdout_text` that is a JSON
    object carrying one — i.e. what makes the text an **event stream**.
    Empty for plain text, so `assertEqual([], ...)` reads as "not a
    stream"."""
    kinds = []
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and "event" in event:
            kinds.append(event["event"])
    return kinds


# Every **fake agy** behavior whose stdout is (or, under `--output-format
# stream-json`, becomes) an **event stream**. Appended to, never reordered,
# as the fake grows.
_STREAMING_BEHAVIORS = (
    ("delegate_background_finished", "delegate"),
    ("delegate_resume_bound", "delegate_resume"),
    ("delegate_resume_silent_fallback", "delegate_resume"),
    ("review_bound_valid", "review"),
    ("review_background_finished", "review"),
    ("review_stream_no_result", "review"),
)


def _streaming_argv(shape, log_file, stream_json):
    # --disable-slash-commands and --print-timeout are both unconditional on
    # every review-launch and delegate-launch argv — omitting either here
    # would trip FakeAgyDisableSlashCommandsGateTest's / the print-timeout
    # gate before any behavior below ever ran. The print-timeout value
    # itself is never read by this file's assertions, only its presence.
    if shape == "review":
        argv = [
            "-p", "task", "--disable-slash-commands",
            "--agent", "agy-review", "--sandbox", "--new-project",
        ]
    elif shape == "delegate_resume":
        argv = [
            "-p", "task", "--disable-slash-commands",
            "--dangerously-skip-permissions", "--sandbox",
            "--conversation", "12345678-1234-4123-8123-123456789abc",
        ]
    else:
        argv = [
            "-p", "task", "--disable-slash-commands",
            "--dangerously-skip-permissions", "--sandbox",
            "--new-project",
        ]
    if stream_json:
        argv += ["--output-format", "stream-json"]
    return argv + ["--log-file", str(log_file), "--print-timeout", "290s"]


def _stage_workspace(tmp):
    """A cwd where the workspace-scoped `agy-review` agent resolves, so the
    review behaviors are not diverted into the fallback branch."""
    workspace = Path(tmp)
    agent_dir = workspace / ".agents" / "agents" / "agy-review"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.md").write_text("stub\n", encoding="utf-8")
    return workspace


class FakeAgyStreamJsonGateTest(unittest.TestCase):
    """The **fake agy** must never be more generous than the binary.

    `agy` prints an **event stream** only under `--output-format
    stream-json`; a launch without that flag gets plain text. A fake that
    emits a stream unconditionally makes every downstream consumer look
    correct on a launch path that would, against the real binary, hand them
    plain text — which is exactly how a background **delegate** **job**
    came to be asserted `completed` by
    `tests/test_cross_command_integration.py` while
    `status.derive_status` would have derived `running` forever in real use.

    Held by invoking the fake, never by grepping its source text: a fake is
    only pinned by what it actually writes to stdout.
    """

    def test_no_event_stream_without_the_output_format_flag(self):
        for behavior, shape in _STREAMING_BEHAVIORS:
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as tmp:
                workspace = _stage_workspace(tmp)
                log_file = workspace / "agy.log"
                proc = _run_fake_agy(
                    behavior, _streaming_argv(shape, log_file, stream_json=False), workspace
                )
                self.assertEqual(
                    [], _event_kinds(proc.stdout),
                    "fake agy emitted an event stream on a launch whose argv never "
                    "asked for --output-format stream-json",
                )

    def test_event_stream_when_the_output_format_flag_is_present(self):
        for behavior, shape in _STREAMING_BEHAVIORS:
            with self.subTest(behavior=behavior), tempfile.TemporaryDirectory() as tmp:
                workspace = _stage_workspace(tmp)
                log_file = workspace / "agy.log"
                proc = _run_fake_agy(
                    behavior, _streaming_argv(shape, log_file, stream_json=True), workspace
                )
                self.assertIn(
                    "init", _event_kinds(proc.stdout),
                    "fake agy produced no event stream under --output-format stream-json",
                )


class FakeAgyDisableSlashCommandsGateTest(unittest.TestCase):
    """`delegate._agy_command` and `review._agy_command` send
    `--disable-slash-commands` on EVERY launch, unconditionally — see
    `.looper/knowledge/glossary.md`'s **slash-command expansion** entry. A
    review-launch or delegate-launch argv missing that flag is a shape the
    real companion-built command vector would never produce, so the fake
    must refuse to stand in for it (loudly, not silently) rather than grade
    the companion against itself — the same discipline
    `FakeAgyStreamJsonGateTest` holds for `--output-format stream-json`.

    The **bind-probe launch** (`--model <invalid>`, `setup.py`'s zero-quota
    diagnostic) is deliberately excluded — out of this mission's scope, see
    M6_purpose.md's soft criteria."""

    def test_review_launch_without_the_flag_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _stage_workspace(tmp)
            log_file = workspace / "agy.log"
            argv = [
                "-p", "task", "--agent", "agy-review", "--sandbox", "--new-project",
                "--log-file", str(log_file),
            ]
            proc = _run_fake_agy("review_bound_valid", argv, workspace)
            self.assertNotEqual(
                0, proc.returncode,
                "fake agy accepted a review-launch argv missing "
                "--disable-slash-commands",
            )
            self.assertIn("--disable-slash-commands", proc.stderr)

    def test_review_launch_with_the_flag_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _stage_workspace(tmp)
            log_file = workspace / "agy.log"
            argv = [
                "-p", "task", "--agent", "agy-review", "--sandbox", "--new-project",
                "--disable-slash-commands",
                "--log-file", str(log_file), "--print-timeout", "290s",
            ]
            proc = _run_fake_agy("review_bound_valid", argv, workspace)
            self.assertEqual(0, proc.returncode, proc.stderr)

    def test_delegate_launch_without_the_flag_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            log_file = workspace / "agy.log"
            argv = [
                "-p", "task", "--dangerously-skip-permissions", "--sandbox",
                "--new-project", "--log-file", str(log_file),
            ]
            proc = _run_fake_agy("delegate_fresh_bound", argv, workspace)
            self.assertNotEqual(
                0, proc.returncode,
                "fake agy accepted a delegate-launch argv missing "
                "--disable-slash-commands",
            )
            self.assertIn("--disable-slash-commands", proc.stderr)

    def test_delegate_launch_with_the_flag_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            log_file = workspace / "agy.log"
            argv = [
                "-p", "task", "--dangerously-skip-permissions", "--sandbox",
                "--new-project", "--disable-slash-commands", "--log-file", str(log_file),
                "--print-timeout", "290s",
            ]
            proc = _run_fake_agy("delegate_fresh_bound", argv, workspace)
            self.assertEqual(0, proc.returncode, proc.stderr)


def _field_names(events):
    """The dotted field names `events` carry, for set comparison. Payload
    fields read `result.status` / `init.agent` / `step_update.step_index`;
    a field alongside the payload reads `init[conversation_id]`; **usage**
    token keys read `result.usage.input_tokens`."""
    names = set()
    for event in events:
        kind = event.get("event")
        for key, value in event.items():
            if key == "event":
                continue
            if key == kind and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    names.add("{}.{}".format(kind, sub_key))
                    if sub_key == "usage" and isinstance(sub_value, dict):
                        for token_key in sub_value:
                            names.add("{}.usage.{}".format(kind, token_key))
            else:
                names.add("{}[{}]".format(kind, key))
    return names


def _json_lines(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and "event" in event:
            events.append(event)
    return events


class FakeAgyEventVocabularyTest(unittest.TestCase):
    """The **fake agy**'s three **event stream** synthesizers, measured
    against the vocabulary of the two real captures.

    The subject is the fake; the reference is the real bytes — never the
    other way round. Two failure modes, both of which would make every
    consumer test in this suite agree with a fiction:

      1. the fake invents a field `agy` never emitted, so a consumer can
         start reading it and nothing notices;
      2. the fake omits a field the companion actually reads, so the
         consumer's handling of it is exercised by no test at all.

    Held by invoking the fake, never by reading its source."""

    # What the companion reads out of a **result event** — mirrors
    # `stream_events.EventStream`'s fields.
    _COMPANION_READS = {
        "result.conversation_id", "result.status", "result.response", "result.usage",
        "step_update.step_index", "step_update.step_type", "step_update.state",
    }

    def _fake_field_names(self):
        names = set()
        for behavior, shape in _STREAMING_BEHAVIORS:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = _stage_workspace(tmp)
                argv = _streaming_argv(shape, workspace / "agy.log", stream_json=True)
                proc = _run_fake_agy(behavior, argv, workspace)
                events = _json_lines(proc.stdout)
                self.assertTrue(events, "{} emitted no event stream".format(behavior))
                names |= _field_names(events)
        return names

    def test_fake_invents_no_field_agy_never_emitted(self):
        real = _field_names(_raw_events(_STREAM_EVENTS_CAPTURE)) | _field_names(
            _raw_events(_ADVERSARIAL_CAPTURE)
        )
        invented = self._fake_field_names() - real
        self.assertEqual(
            set(), invented,
            "fake agy emits event fields absent from both real captures: {}".format(
                sorted(invented)
            ),
        )

    def test_fake_emits_every_field_the_companion_reads(self):
        """Union across the synthesizers, not per-behavior: a truncated
        stream legitimately stops before its **result event**."""
        missing = self._COMPANION_READS - self._fake_field_names()
        self.assertEqual(
            set(), missing,
            "fake agy omits fields the companion reads: {}".format(sorted(missing)),
        )

    def test_the_companion_reads_only_fields_the_real_captures_carry(self):
        """The other end of the same coupling: every field name the shipped
        parser promotes is one `agy` really wrote."""
        real = _field_names(_raw_events(_STREAM_EVENTS_CAPTURE)) | _field_names(
            _raw_events(_ADVERSARIAL_CAPTURE)
        )
        self.assertLessEqual(self._COMPANION_READS, real)


# --- the **agent fallback** capture ---
#
# A single real capture of `agy` given a deliberately nonexistent `--agent`
# value. Not part of `_CAPTURES`
# above: that tuple's tests assume a successful review-schema run, and this
# is a plain "say hello" run whose `result.response` is text, not JSON.
# Every value below is transcribed from the fixture's own PROVENANCE.md,
# same discipline as `_STREAM_EVENTS_CAPTURE` / `_ADVERSARIAL_CAPTURE`.

_AGENT_FALLBACK_PATH = REPO_ROOT / "tests" / "fixtures" / "agent_fallback" / "2026-08-02-run1.ndjson"
_AGENT_FALLBACK_REQUESTED_AGENT = "definitely-nonexistent-agent-xyz"
_AGENT_FALLBACK_CONVERSATION = "d0ed0156-0de8-43de-ae2a-a3fbb7f9596a"


def _agent_fallback_events():
    return [
        json.loads(line)
        for line in _read(_AGENT_FALLBACK_PATH).splitlines()
        if line.strip()
    ]


class AgentFallbackEventStreamTest(unittest.TestCase):
    """a real **event stream** for a run whose
    `--agent` value does not exist, held against real captured bytes.

    The **log bind proof** for this same run (not committed — see
    PROVENANCE.md) shows `agy` printing `Agent "definitely-nonexistent-
    agent-xyz" not found, falling back to default` — the fallback is real.
    But the **init event** below does NOT read back the resolved default;
    it echoes the REQUESTED bogus name. That is the load-bearing negative
    finding this fixture exists to pin: `init.agent` is not evidence of
    which agent bound, only of which agent was asked for."""

    def test_event_kind_sequence(self):
        events = _agent_fallback_events()
        kinds = [event.get("event") for event in events]
        self.assertEqual(6, len(events))
        self.assertEqual(
            ["init", "step_update", "step_update", "step_update", "step_update", "result"],
            kinds,
        )

    def test_init_event_key_set(self):
        init = _agent_fallback_events()[0]
        self.assertEqual({"event", "conversation_id", "init"}, set(init))
        self.assertEqual({"agent", "cwd", "permission_mode", "tools"}, set(init["init"]))
        self.assertEqual(_AGENT_FALLBACK_CONVERSATION, init["conversation_id"])

    def test_init_agent_echoes_the_requested_bogus_name_not_a_resolved_default(self):
        """A real **agent fallback** run's
        **init event** carries `init.agent` equal to the REQUESTED
        (nonexistent) name, not the agent that actually resolved — a
        positive, hard-coded equality, not merely "not equal to the bogus
        name". This is why this repo cannot build a **structural bind proof** by
        comparing the requested `--agent` value against `init.agent` alone:
        that comparison would read this exact run as a successful bind."""
        init = _agent_fallback_events()[0]
        self.assertEqual(_AGENT_FALLBACK_REQUESTED_AGENT, init["init"]["agent"])

    def test_result_event_reads_success(self):
        result = _agent_fallback_events()[-1]
        self.assertEqual({"event", "result"}, set(result))
        self.assertEqual("SUCCESS", result["result"]["status"])
        self.assertEqual(_AGENT_FALLBACK_CONVERSATION, result["result"]["conversation_id"])

    def test_parses_with_the_shipped_parser(self):
        """The **event stream** parser must not choke on a non-review
        response (plain text, not JSON) or on the step shape this run
        exercises (single-line steps, an unfamiliar `step_type`)."""
        stream = parse_event_stream(_read(_AGENT_FALLBACK_PATH))
        self.assertEqual(_AGENT_FALLBACK_CONVERSATION, stream.conversation_id)
        self.assertEqual("SUCCESS", stream.status)
        self.assertEqual(4, len(stream.step_updates))
        self.assertIsNone(
            stream.parsed_response(),
            "a plain-text response should not parse as the review JSON schema",
        )


# --- the **resume** capture ---
#
# A genuine **resume** (`--conversation <valid-uuid>`,
# `--output-format stream-json`) — the first committed **event stream**
# witnessing this path; every prior resume evidence
# (`tests/fixtures/delegate/2026-07-30-resume-genuine.log`) is klog TEXT,
# not an **event stream**. See tests/fixtures/resume/PROVENANCE.md.

_RESUME_PATH = REPO_ROOT / "tests" / "fixtures" / "resume" / "2026-08-02-run1.ndjson"
_RESUME_CONVERSATION = "a4425612-2b6c-4e0c-a9b5-e7600418be81"


def _resume_events():
    return [json.loads(line) for line in _read(_RESUME_PATH).splitlines() if line.strip()]


class ResumeEventStreamTest(unittest.TestCase):
    """a real **event stream** for a genuine
    **resume**, held against real captured bytes. Structurally distinct
    from every `--agent`-passing capture: the **init event** carries no
    `"agent"` key at all, present or null — `delegate._agy_command` never
    passes `--agent`."""

    def test_event_kind_sequence(self):
        events = _resume_events()
        kinds = [event.get("event") for event in events]
        self.assertEqual(6, len(events))
        self.assertEqual(
            ["init", "step_update", "step_update", "step_update", "step_update", "result"],
            kinds,
        )

    def test_init_event_key_set_has_no_agent_key(self):
        init = _resume_events()[0]
        self.assertEqual({"event", "conversation_id", "init"}, set(init))
        self.assertEqual({"cwd", "permission_mode", "tools"}, set(init["init"]))
        self.assertEqual(_RESUME_CONVERSATION, init["conversation_id"])

    def test_result_event_resolves_to_the_requested_conversation(self):
        """The structural **bind proof** a genuine resume carries: the
        result event's `conversation_id` equals the REQUESTED
        `--conversation` value — the same equality `_resume_bind_check`
        checks."""
        result = _resume_events()[-1]
        self.assertEqual({"event", "result"}, set(result))
        self.assertEqual("SUCCESS", result["result"]["status"])
        self.assertEqual(_RESUME_CONVERSATION, result["result"]["conversation_id"])

    def test_parses_with_the_shipped_parser(self):
        stream = parse_event_stream(_read(_RESUME_PATH))
        self.assertEqual(_RESUME_CONVERSATION, stream.conversation_id)
        self.assertEqual("SUCCESS", stream.status)
        self.assertEqual(4, len(stream.step_updates))

    def test_resume_bind_check_binds_the_real_resumed_conversation(self):
        stream = parse_event_stream(_read(_RESUME_PATH))
        bound, proof = _resume_bind_check(stream, _RESUME_CONVERSATION)
        self.assertTrue(bound, "a real genuine resume failed to prove its own bind")
        self.assertEqual(_RESUME_CONVERSATION, proof)


# --- the **resume fallback** capture ---
#
# A real **resume fallback** (`--conversation <bogus-uuid>`,
# `--output-format stream-json`). See
# tests/fixtures/resume_fallback/PROVENANCE.md for the free zero-quota
# probe's negative finding this fixture's paid capture supersedes: the free
# probe (bogus conversation + bogus model together) produces an ERROR
# result event, but one shaped by the bogus MODEL, with no init event at
# all — not usable evidence of what a resume fallback's own event stream
# looks like. This fixture is the real, paid, positive answer.

_RESUME_FALLBACK_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "resume_fallback" / "2026-08-02-run1.ndjson"
)
_RESUME_FALLBACK_REQUESTED = "00000000-0000-4000-8000-000000000000"
_RESUME_FALLBACK_ACTUAL = "dbf4949e-4897-4735-ba30-efa865b105a2"


def _resume_fallback_events():
    return [
        json.loads(line) for line in _read(_RESUME_FALLBACK_PATH).splitlines() if line.strip()
    ]


class ResumeFallbackEventStreamTest(unittest.TestCase):
    """a real **event stream** for a
    **resume fallback**, held against real captured bytes. Unlike **agent
    fallback** (`tests/fixtures/agent_fallback/`, where `init.agent` echoes
    the REQUESTED bogus value and cannot detect the fallback), a **resume
    fallback** DOES carry a structural tell: the stream's
    `conversation_id` is the real new conversation, never the bogus
    requested one."""

    def test_event_kind_sequence(self):
        events = _resume_fallback_events()
        kinds = [event.get("event") for event in events]
        self.assertEqual(6, len(events))
        self.assertEqual(
            ["init", "step_update", "step_update", "step_update", "step_update", "result"],
            kinds,
        )

    def test_init_conversation_id_is_the_new_conversation_not_the_bogus_request(self):
        init = _resume_fallback_events()[0]
        self.assertEqual({"event", "conversation_id", "init"}, set(init))
        self.assertEqual({"cwd", "permission_mode", "tools"}, set(init["init"]))
        self.assertEqual(_RESUME_FALLBACK_ACTUAL, init["conversation_id"])
        self.assertNotEqual(_RESUME_FALLBACK_REQUESTED, init["conversation_id"])

    def test_parses_with_the_shipped_parser(self):
        stream = parse_event_stream(_read(_RESUME_FALLBACK_PATH))
        self.assertEqual(_RESUME_FALLBACK_ACTUAL, stream.conversation_id)
        self.assertEqual("SUCCESS", stream.status)

    def test_resume_bind_check_rejects_the_bogus_requested_conversation(self):
        """The structural detector a resume fallback needs: comparing the
        REQUESTED `--conversation` value against the stream's own
        `conversation_id` correctly reports NOT bound, with no
        `--log-file` needed — unlike **agent fallback**, where no such
        structural check exists at all (glossary: **bind proof**)."""
        stream = parse_event_stream(_read(_RESUME_FALLBACK_PATH))
        bound, proof = _resume_bind_check(stream, _RESUME_FALLBACK_REQUESTED)
        self.assertFalse(bound, "a resume fallback would read as a successful resume")
        self.assertEqual(_RESUME_FALLBACK_ACTUAL, proof)


# --- the **error_result** capture ---
#
# The first real captured ERROR-status **result event**. See
# tests/fixtures/error_result/PROVENANCE.md for the full writeup, including
# the finding that corrects `.looper/knowledge/glossary.md`'s "fully
# populated usage block" claim — this real ERROR's **usage** is all-zero,
# not fully populated. Mirrors
# `tests/test_result.py`'s `ResultForJobCrashedResultEventTest`, but against
# these real bytes rather than hand-written synthetic NDJSON.

_ERROR_RESULT_PATH = REPO_ROOT / "tests" / "fixtures" / "error_result" / "2026-08-02-run1.ndjson"
_ERROR_RESULT_CONVERSATION = "6bfaf836-0988-4b34-ac6a-0073c7747bde"


def _error_result_events():
    return [
        json.loads(line) for line in _read(_ERROR_RESULT_PATH).splitlines() if line.strip()
    ]


class RealErrorResultEventTest(unittest.TestCase):
    """The real ERROR **result event**'s exact key set and values, held
    against real bytes captured via `--print-timeout` — no
    synthetic NDJSON. Also runs the real shipped ERROR-rendering path
    (`companion.result._render_result`) directly over these real bytes,
    confirming it renders `status` and `error` and never the raw NDJSON
    dump."""

    def test_event_kind_sequence_is_shorter_than_a_success_run(self):
        """No `agent_response` or `checkpoint` step ever started — the
        timeout preempted the run before generation began streaming."""
        events = _error_result_events()
        kinds = [event.get("event") for event in events]
        self.assertEqual(["init", "step_update", "step_update", "result"], kinds)

    def test_result_event_key_set_and_values(self):
        result = _error_result_events()[-1]
        self.assertEqual({"event", "result"}, set(result))
        payload = result["result"]
        self.assertEqual(
            {
                "conversation_id", "status", "response", "error",
                "duration_seconds", "num_turns", "usage",
            },
            set(payload),
        )
        self.assertEqual(_ERROR_RESULT_CONVERSATION, payload["conversation_id"])
        self.assertEqual("ERROR", payload["status"])
        self.assertEqual("", payload["response"])
        self.assertEqual("timeout waiting for response", payload["error"])

    def test_usage_block_is_all_zero_not_fully_populated(self):
        """The specific finding that corrects glossary.md's **result
        event** entry: a `--print-timeout` expiry before any
        `agent_response` step carries an all-zero **usage** block, not a
        "fully populated" one (`.looper/knowledge/glossary.md`'s current
        text, written before any real ERROR bytes existed)."""
        result = _error_result_events()[-1]
        self.assertEqual(
            {
                "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
                "cache_read_tokens": 0, "total_tokens": 0,
            },
            result["result"]["usage"],
        )

    def test_parses_with_the_shipped_parser(self):
        stream = parse_event_stream(_read(_ERROR_RESULT_PATH))
        self.assertEqual(_ERROR_RESULT_CONVERSATION, stream.conversation_id)
        self.assertEqual("ERROR", stream.status)
        self.assertEqual("", stream.response)
        self.assertEqual("timeout waiting for response", stream.error)

    def test_render_result_renders_status_and_error_never_the_raw_dump(self):
        """The real shipped ERROR-rendering path, run directly over these
        real bytes — the exact thing the epic's honesty debt was about:
        `ResultForJobCrashedResultEventTest` (tests/test_result.py) only
        ever exercised hand-written synthetic NDJSON built from
        `stream_events.py`'s own field names."""
        job = {
            "id": "job-real-error",
            "kind": "delegate",
            "output_file": str(_ERROR_RESULT_PATH),
        }
        message = _render_result(job)
        self.assertIn("ERROR", message)
        self.assertIn("timeout waiting for response", message)
        self.assertNotIn("conversation_id", message)
        self.assertNotIn('"event"', message)


class FixtureHygieneTest(unittest.TestCase):
    """These fixtures are raw agy output committed to a public repo. agy logs
    the authenticated account on every run (`server_oauth.go:193]`), so a
    capture carries the capturer's identity unless it is scrubbed. That was
    caught once at the last possible moment, by inspection; this is the check
    that does not rely on someone reading far enough down a grep."""

    _ALLOWED_EMAILS = {"user@example.invalid", "scratch@example.invalid"}

    def test_no_real_email_addresses(self):
        pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in sorted(FIXTURES.glob("*.log")):
            found = set(pattern.findall(_read(path))) - self._ALLOWED_EMAILS
            self.assertFalse(
                found, "{} leaks {} — scrub before committing".format(path.name, sorted(found))
            )

    def test_no_home_directory_paths(self):
        for path in sorted(FIXTURES.glob("*.log")):
            text = _read(path)
            for match in re.findall(r"/Users/[^/\s]+", text):
                self.assertEqual(
                    match, "/Users/REDACTED",
                    "{} leaks a home path: {}".format(path.name, match),
                )

    def test_no_real_email_addresses_in_the_event_stream_captures(self):
        pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in _ndjson_fixtures():
            found = set(pattern.findall(_read(path))) - self._ALLOWED_EMAILS
            self.assertFalse(
                found, "{} leaks {} — scrub before committing".format(path.name, sorted(found))
            )

    def test_no_home_or_workspace_paths_in_the_event_stream_captures(self):
        """Both `.ndjson` **capture**s were scrubbed by hand — `init.cwd` in
        one, seven occurrences in the other (see their PROVENANCE.md). This
        asserts the absence of the real shapes rather than the presence of
        the `-REDACTED-WORKSPACE` / `REDACTED-SESSION` placeholders, which a
        half-scrubbed fixture would satisfy too."""
        session_segment = re.compile(
            r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
            r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=[/\"\s])"
        )
        for path in _ndjson_fixtures():
            text = _read(path)
            for match in re.findall(r"/Users/[^/\s\"]+", text):
                self.assertEqual(
                    match, "/Users/REDACTED",
                    "{} leaks a home path: {}".format(path.name, match),
                )
            # The scratch repos live under a claude-501 scratchpad whose path
            # embeds the workspace as `-Users-<name>-workspace-...`.
            self.assertEqual(
                [], re.findall(r"-Users-[^/\s\"]*", text),
                "{} leaks an unredacted workspace path segment".format(path.name),
            )
            self.assertEqual(
                [], session_segment.findall(text),
                "{} leaks an unredacted session path segment".format(path.name),
            )

    def test_both_event_stream_captures_are_in_scope(self):
        """A glob that silently matched nothing would pass every check above.
        Both real **fixture**s must be in scope, by name."""
        scanned = set(_ndjson_fixtures())
        for capture in _CAPTURES:
            self.assertIn(capture["path"], scanned)

    def test_the_capture_tool_scrubs_both(self):
        """The fixtures being clean today is necessary but not sufficient —
        the tool that writes the next one has to scrub on the way in."""
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import live_delegate_capture

        dirty = (
            "I0730 server_oauth.go:193] OAuth: authenticated successfully as "
            "someone@gmail.com\nI0730 cwd=/Users/someone/work\n"
        )
        cleaned = live_delegate_capture.scrub(dirty)
        self.assertNotIn("someone@gmail.com", cleaned)
        self.assertNotIn("/Users/someone", cleaned)
        self.assertIn("user@example.invalid", cleaned)
        self.assertIn("/Users/REDACTED", cleaned)


if __name__ == "__main__":
    unittest.main()
