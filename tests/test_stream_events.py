"""Tests for `companion.stream_events`, held against the real, committed
**event stream** capture in `tests/fixtures/stream_events/` (see
`PROVENANCE.md` there) — never a hand-written NDJSON string standing in for
a capture, per this mission's contract and the discipline `AGENTS.md`
already states for `tests/fake_agy.py`.

Offline and free: reads a committed file, never invokes agy.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.stream_events import EventStream, StepUpdate, parse_event_stream  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "stream_events"
_RUN1 = FIXTURES / "2026-08-02-run1.ndjson"

_DENIED_ACTIONS_RUN1 = (
    REPO_ROOT / "tests" / "fixtures" / "denied_actions" / "2026-09-20-run1.ndjson"
)
_COMMAND_RESULT_RUN1 = (
    REPO_ROOT / "tests" / "fixtures" / "command_result" / "2026-09-20-run1.ndjson"
)

_CAPTURED_UUID = "a4425612-2b6c-4e0c-a9b5-e7600418be81"


def _read(path):
    return path.read_text(encoding="utf-8")


class ParseEventStreamTest(unittest.TestCase):
    def setUp(self):
        self.result = parse_event_stream(_read(_RUN1))

    def test_returns_event_stream_record(self):
        self.assertIsInstance(self.result, EventStream)

    def test_conversation_id_from_result_event(self):
        self.assertEqual(self.result.conversation_id, _CAPTURED_UUID)

    def test_status_from_result_event(self):
        self.assertEqual(self.result.status, "SUCCESS")

    def test_usage_from_result_event(self):
        self.assertIsInstance(self.result.usage, dict)
        self.assertEqual(self.result.usage["input_tokens"], 12308)
        self.assertEqual(self.result.usage["output_tokens"], 1435)
        self.assertEqual(self.result.usage["total_tokens"], 13743)

    def test_response_is_the_raw_result_event_string(self):
        self.assertIsInstance(self.result.response, str)
        self.assertIn('"findings"', self.result.response)
        self.assertIn("Use addition instead of subtraction", self.result.response)

    def test_step_update_sequence_is_ordered_and_real(self):
        # This capture's event_counts (PROVENANCE.md): 1 init, 4 step_update,
        # 1 result -- so exactly 4 step updates, in stream order. Re-captured
        # against agy 1.1.9; the earlier 1.1.8
        # capture this replaced had 5 step updates (one extra partial
        # agent_response chunk) -- streaming-chunk-count variance, not a
        # schema change (see PROVENANCE.md's "did the 1.1.9 stream shape
        # move?" section).
        self.assertEqual(len(self.result.step_updates), 4)
        for step_update in self.result.step_updates:
            self.assertIsInstance(step_update, StepUpdate)

        step_indices = [s.step_index for s in self.result.step_updates]
        self.assertEqual(step_indices, [0, 1, 1, 2])

        step_types = [s.step_type for s in self.result.step_updates]
        self.assertEqual(
            step_types,
            [
                "user_input",
                "agent_response",
                "agent_response",
                "checkpoint",
            ],
        )

        # The final agent_response step update is a real, non-trivial step
        # update: DONE state, carrying duration_seconds and per-step usage.
        final_agent_response = self.result.step_updates[2]
        self.assertEqual(final_agent_response.state, "DONE")
        self.assertIn("duration_seconds", final_agent_response.raw)
        self.assertIn("usage", final_agent_response.raw)

    def test_first_step_update_is_user_input_done(self):
        first = self.result.step_updates[0]
        self.assertEqual(first.step_type, "user_input")
        self.assertEqual(first.state, "DONE")

    def test_denied_actions_absent_from_this_capture_is_an_empty_collection(self):
        # This capture predates denied_actions (added in agy 1.1.27) --
        # the field is absent from its result event entirely. Must render
        # as an empty collection, never None. See
        # tests/fixtures/denied_actions/ for a real capture that DOES
        # carry one.
        self.assertEqual((), self.result.denied_actions)

    def test_command_absent_from_this_capture_is_none(self):
        self.assertIsNone(self.result.command)


class DeniedActionsFixtureTest(unittest.TestCase):
    """`denied_actions` parsing, held against the real **denied action**
    capture in `tests/fixtures/denied_actions/` (see that directory's
    PROVENANCE.md) -- captured via agy's own default agent, never through
    `/agy:review`'s or `/agy:adversarial-review`'s own command vector
    (both vendored review agents declare `tools: []`, so neither can
    produce a denied_actions entry today)."""

    def test_result_event_denied_actions_parsed_from_the_real_capture(self):
        result = parse_event_stream(_read(_DENIED_ACTIONS_RUN1))
        self.assertEqual(
            ({"action": "write_file", "display_name": "WriteToFile"},),
            result.denied_actions,
        )

    def test_status_is_success_despite_the_denial(self):
        # A denied action is not fatal -- the run still exits 0 and still
        # reports a verdict (glossary: **degraded**, not an error).
        result = parse_event_stream(_read(_DENIED_ACTIONS_RUN1))
        self.assertEqual("SUCCESS", result.status)

    def test_response_is_the_empty_string_not_none(self):
        result = parse_event_stream(_read(_DENIED_ACTIONS_RUN1))
        self.assertEqual("", result.response)


class CommandResultFixtureTest(unittest.TestCase):
    """`command_result` event recognition, held against the real capture in
    `tests/fixtures/command_result/` (see that directory's PROVENANCE.md)."""

    _EXPECTED_COMMAND = {
        "name": "model",
        "data": {
            "id": "gemini-3.8-flash-medium",
            "label": "Gemini 3.8 Flash (Medium)",
            "effort": "medium",
            "is_default": False,
        },
    }

    def test_command_parsed_from_the_dedicated_command_result_event(self):
        result = parse_event_stream(_read(_COMMAND_RESULT_RUN1))
        self.assertEqual(self._EXPECTED_COMMAND, result.command)

    def test_response_is_the_plain_text_answer(self):
        result = parse_event_stream(_read(_COMMAND_RESULT_RUN1))
        self.assertEqual("gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n", result.response)

    def test_stream_with_no_init_event_still_parses(self):
        # A read-only slash-command probe never starts a conversation, so
        # this real capture has no init event at all -- the parser must
        # not assume one always arrives first.
        result = parse_event_stream(_read(_COMMAND_RESULT_RUN1))
        self.assertEqual("SUCCESS", result.status)

    def test_result_events_own_command_key_matches_the_dedicated_event(self):
        # Both the dedicated command_result line and the terminal result
        # event's own `command` key carry the same payload in this real
        # capture -- a stream truncated before the dedicated line must
        # still recover the command from the result event alone. Proven
        # directly here by reading the command_result-only case below.
        lines = _read(_COMMAND_RESULT_RUN1).splitlines()
        result_only = lines[-1]  # the terminal result event alone
        result = parse_event_stream(result_only)
        self.assertEqual(self._EXPECTED_COMMAND, result.command)


class UnknownEventKindStillIgnoredTest(unittest.TestCase):
    """Hand-written, synthetic -- pins that a genuinely unknown event kind
    (never `init`/`step_update`/`result`/`command_result`) is still
    silently ignored exactly as before, per the module's own documented
    "unknown kinds are ignored" design."""

    def test_genuinely_unknown_event_kind_is_ignored_not_rejected(self):
        line = '{"event": "some_future_event_kind", "some_future_event_kind": {"x": 1}}'
        result = parse_event_stream(line)
        self.assertIsNone(result.status)
        self.assertEqual((), result.denied_actions)
        self.assertIsNone(result.command)


class ParsedResponseTest(unittest.TestCase):
    """`EventStream.parsed_response()` — the **tolerant parse** of
    `response`'s content, load-bearing per `docs/json-schema-verdict.md`."""

    def test_parses_the_captured_clean_response(self):
        result = parse_event_stream(_read(_RUN1))
        parsed = result.parsed_response()
        self.assertIsInstance(parsed, dict)
        self.assertIn("findings", parsed)
        self.assertEqual(parsed["overall_correctness"], "patch is incorrect")
        self.assertEqual(len(parsed["findings"]), 1)

    def test_absent_response_returns_none_not_raise(self):
        record = EventStream(
            conversation_id="x", status="SUCCESS", response=None, usage=None, step_updates=()
        )
        self.assertIsNone(record.parsed_response())

    def test_malformed_response_returns_none_not_raise(self):
        record = EventStream(
            conversation_id="x",
            status="SUCCESS",
            response="not json at all {",
            usage=None,
            step_updates=(),
        )
        self.assertIsNone(record.parsed_response())

    def test_concatenated_objects_response_returns_first_object_not_raise(self):
        # Per docs/json-schema-verdict.md: with --json-schema, `response` can
        # come back as several JSON objects concatenated with no delimiter,
        # which fails json.loads(response) outright. This module must not
        # hard-fail on that shape either.
        record = EventStream(
            conversation_id="x",
            status="SUCCESS",
            response='{"a": 1}\n{"a": 2}\n',
            usage=None,
            step_updates=(),
        )
        # Never raises; extracts the first balanced object rather than
        # attempting (and failing) a whole-string json.loads.
        self.assertEqual(record.parsed_response(), {"a": 1})

    def test_structured_output_preferred_when_present(self):
        # When agy emits structured_output (e.g. from --json-schema),
        # parsed_response returns it directly.
        record = EventStream(
            conversation_id="x",
            status="SUCCESS",
            response='raw unparsed text',
            usage=None,
            step_updates=(),
            structured_output={"verdict": "pass", "count": 42},
        )
        self.assertEqual(record.parsed_response(), {"verdict": "pass", "count": 42})


class EmptyAndMinimalStreamTest(unittest.TestCase):
    def test_empty_text_returns_all_none_and_no_step_updates(self):
        result = parse_event_stream("")
        self.assertIsNone(result.conversation_id)
        self.assertIsNone(result.status)
        self.assertIsNone(result.response)
        self.assertIsNone(result.usage)
        self.assertEqual(result.step_updates, ())

    def test_init_only_stream_falls_back_to_init_conversation_id(self):
        init_line = (
            '{"event": "init", "conversation_id": "abc-123", '
            '"init": {"cwd": "/tmp", "agent": "agy-review"}}'
        )
        result = parse_event_stream(init_line)
        self.assertEqual(result.conversation_id, "abc-123")
        self.assertIsNone(result.status)

    def test_error_is_none_when_absent_from_the_result_event(self):
        # Hand-written, synthetic -- not a capture (same discipline as
        # test_init_only_stream_falls_back_to_init_conversation_id above).
        # Every real fixture committed today (including this one's shape)
        # has no `error` key on its result event, so this pins the default.
        result_line = '{"event": "result", "result": {"status": "SUCCESS"}}'
        result = parse_event_stream(result_line)
        self.assertIsNone(result.error)

    def test_error_is_populated_when_present_on_the_result_event(self):
        # Hand-written, synthetic -- this only pins that the field is read
        # through when present. A real non-SUCCESS result event with a real
        # `error` string now exists
        # (tests/fixtures/error_result/2026-08-02-run1.ndjson) and is
        # asserted directly by
        # tests/test_log_fidelity.py's RealErrorResultEventTest.
        result_line = (
            '{"event": "result", "result": {"status": "ERROR", '
            '"error": "killed: run exceeded print timeout"}}'
        )
        result = parse_event_stream(result_line)
        self.assertEqual(result.status, "ERROR")
        self.assertEqual(result.error, "killed: run exceeded print timeout")

    def test_structured_output_is_populated_when_present_on_result_event(self):
        result_line = (
            '{"event": "result", "result": {"status": "SUCCESS", '
            '"structured_output": {"findings": []}}}'
        )
        result = parse_event_stream(result_line)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.structured_output, {"findings": []})

    def test_denied_actions_absent_on_a_hand_written_result_event_is_empty(self):
        # Hand-written, synthetic -- pins the default independent of any
        # captured fixture.
        result_line = '{"event": "result", "result": {"status": "SUCCESS"}}'
        result = parse_event_stream(result_line)
        self.assertEqual((), result.denied_actions)

    def test_denied_actions_present_but_empty_on_the_result_event_stays_empty(self):
        # Hand-written, synthetic -- an explicit empty list must render
        # identically to an absent key, never None either way.
        result_line = '{"event": "result", "result": {"status": "SUCCESS", "denied_actions": []}}'
        result = parse_event_stream(result_line)
        self.assertEqual((), result.denied_actions)

    def test_command_result_event_populates_command(self):
        # Hand-written, synthetic -- pins the dedicated command_result
        # event kind's field name independent of any captured fixture.
        line = (
            '{"event": "command_result", "command": {"name": "usage", "data": {"x": 1}}}'
        )
        result = parse_event_stream(line)
        self.assertEqual({"name": "usage", "data": {"x": 1}}, result.command)


if __name__ == "__main__":
    unittest.main()
