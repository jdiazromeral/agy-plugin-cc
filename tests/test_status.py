"""Tests for companion.status — job status derived from the raw `--log-file`
text (silent fallback only) plus the job's parsed **event stream**, and the
/agy:status table. Driven directly over log-text strings, synthetic
event-stream NDJSON, the real committed fixture, and job records (pure
functions plus temp-file `--log-file`/`output_file`) — no subprocess, no
agy, no network. `test_status_live.py` (a later slice) exercises the
subcommand's end-to-end reading of a real state dir.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.agy_log import find_conversation  # noqa: E402
from companion.status import (  # noqa: E402
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_CRASHED,
    STATUS_RUNNING,
    STATUS_SILENT_FALLBACK,
    build_job_row,
    derive_status,
    render_status_table,
)
from companion.stream_events import parse_event_stream  # noqa: E402

_BOUND_UUID = "22222222-2222-2222-2222-222222222222"

_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "stream_events" / "2026-08-02-run1.ndjson"
)

_RUNNING_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    "I0101 00:00:00.000000       1 server.go:934] Created conversation {}\n"
).format(_BOUND_UUID)

_SILENT_FALLBACK_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    'W0101 00:00:00.000000       1 printmode.go:166] Agent "agy-review" not found, '
    "falling back to default\n"
)


def _result_event_ndjson(status, response=None, conversation_id=_BOUND_UUID, usage=None):
    """A minimal, synthetic **event stream** — one **result event** line,
    built with `json.dumps` against the field names `stream_events.py`'s
    own dataclasses use (`conversation_id`, `status`, `response`, `usage` —
    see the real fixture below for the genuine shape). NOT a claimed real
    trace: exercises status/usage branches (non-SUCCESS, absent) the real
    fixture (SUCCESS only) cannot reach on its own."""
    return (
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": conversation_id,
                    "status": status,
                    "response": response,
                    "usage": usage,
                },
            }
        )
        + "\n"
    )


def _result_event_stream(status, response=None, conversation_id=_BOUND_UUID, usage=None):
    return parse_event_stream(_result_event_ndjson(status, response, conversation_id, usage))


class DeriveStatusTest(unittest.TestCase):
    """The status states derive_status must distinguish: silent fallback
    (log-derived, unchanged by this mission), completed/crashed
    (event-stream-derived, from the **result event**'s `status`), and
    running (no result event reached yet)."""

    def test_no_event_stream_is_running(self):
        self.assertEqual(derive_status(_RUNNING_LOG, None), STATUS_RUNNING)

    def test_empty_log_and_no_event_stream_is_running(self):
        self.assertEqual(derive_status("", None), STATUS_RUNNING)

    def test_result_event_status_success_is_completed(self):
        stream = _result_event_stream("SUCCESS", response="a review")
        self.assertEqual(derive_status(_RUNNING_LOG, stream), STATUS_COMPLETED)

    def test_fallback_trace_line_is_silent_fallback_error_not_completed(self):
        self.assertEqual(derive_status(_SILENT_FALLBACK_LOG, None), STATUS_SILENT_FALLBACK)

    def test_result_event_status_not_success_is_bound_but_crashed(self):
        """"FAILED" is synthetic-for-branch-coverage only — no real crash
        `result.status` value has ever been observed live (per the
        contract). This exercises "present and not SUCCESS", generically;
        no specific failure string is special-cased in derive_status."""
        stream = _result_event_stream("FAILED")
        self.assertEqual(derive_status(_RUNNING_LOG, stream), STATUS_CRASHED)

    def test_fallback_always_wins_even_if_the_event_stream_says_completed(self):
        """AGENTS.md: 'Never trust an agy run that silently fell back' — a
        fallback trace must never be shadowed by a completed event stream,
        since a fallback run's output is not a real result no matter what
        else was captured."""
        stream = _result_event_stream("SUCCESS", response="a review")
        self.assertEqual(derive_status(_SILENT_FALLBACK_LOG, stream), STATUS_SILENT_FALLBACK)


class FindConversationTest(unittest.TestCase):
    """companion.agy_log.find_conversation itself — untouched by this
    mission, still the log-derived **bind** signal build_job_row reports."""

    def test_extracts_uuid_from_created_conversation_line(self):
        self.assertEqual(find_conversation(_RUNNING_LOG), _BOUND_UUID)

    def test_extracts_uuid_from_completion_marker_when_bind_line_absent(self):
        marker_only = (
            "I0101 00:00:05.000000       1 stream.go:42] Stream completed for "
            "{}, clearing ResponsePending\n"
        ).format(_BOUND_UUID)
        self.assertEqual(find_conversation(marker_only), _BOUND_UUID)

    def test_none_when_no_uuid_bearing_line_present(self):
        self.assertIsNone(find_conversation(_SILENT_FALLBACK_LOG))


class BuildJobRowTest(unittest.TestCase):
    def _write_file(self, tmp, name, text):
        path = Path(tmp) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _write_log(self, tmp, text):
        return self._write_file(tmp, "job.log", text)

    def _write_output(self, tmp, text):
        return self._write_file(tmp, "job.out", text)

    def test_running_job_row_has_no_terminal_status_and_the_bound_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            job = {
                "id": "job-1", "kind": "review", "status": "running",
                "log_file": log_file, "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_RUNNING)
        self.assertEqual(row["conversation"], _BOUND_UUID)
        self.assertIsNotNone(row["elapsed"])

    def test_completed_job_row_reads_status_from_the_result_events_status_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(
                tmp, _result_event_ndjson("SUCCESS", response="ok")
            )
            job = {
                "id": "job-2", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_COMPLETED)
        self.assertEqual(row["conversation"], _BOUND_UUID)

    def test_result_event_status_not_success_reads_as_crashed(self):
        """Synthetic status ("FAILED") — see DeriveStatusTest's note; no
        real crash `result.status` has ever been observed live."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(
                tmp, _result_event_ndjson("FAILED", response=None)
            )
            job = {
                "id": "job-crashed", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_CRASHED)

    def test_real_fixture_output_file_parses_to_a_completed_row(self):
        """Genuine captured bytes
        (tests/fixtures/stream_events/PROVENANCE.md), used verbatim as
        `output_file` content — proving the parse path works end to end
        against real agy output, not just synthetic NDJSON."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(tmp, _FIXTURE.read_text(encoding="utf-8"))
            job = {
                "id": "job-fixture", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_COMPLETED)

    def test_missing_log_file_and_no_output_file_reads_as_running_not_a_crash(self):
        job = {
            "id": "job-3", "kind": "review", "status": "queued",
            "log_file": "/nonexistent/path/does-not-exist.log",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_RUNNING)
        self.assertIsNone(row["conversation"])

    def test_malformed_output_file_never_raises_and_reads_as_running(self):
        """Acceptance criteria: build_job_row must never raise on empty,
        partial, or unparseable `output_file` content — any parse failure
        degrades to "no result event" (running), never a crash of
        /agy:status itself."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(tmp, "not ndjson at all\nneither is this{\n")
            job = {
                "id": "job-malformed", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_RUNNING)

    def test_cancelled_job_row_reports_cancelled_even_though_no_result_event_landed(self):
        """The log/event stream can never express `cancelled` — there is no
        babysitter to write anything after /agy:cancel signals the process.
        A stored `status: cancelled` must be authoritative and short-circuit
        derivation, which would otherwise still read STATUS_RUNNING here."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            job = {
                "id": "job-5", "kind": "review", "status": "cancelled",
                "log_file": log_file, "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_CANCELLED)

    def test_stored_running_status_is_never_trusted_over_a_completed_event_stream(self):
        """The inverse of the cancelled short-circuit: a stale stored
        `running` status must still be re-derived, never trusted as-is."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(
                tmp, _result_event_ndjson("SUCCESS", response="ok")
            )
            job = {
                "id": "job-6", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["status"], STATUS_COMPLETED)

    def test_log_tail_is_the_last_non_empty_line_of_the_log_regardless_of_status(self):
        """log_tail is a log-derived display field, independent of the
        event-stream-derived status — mechanism-independent guarantee."""
        with tempfile.TemporaryDirectory() as tmp:
            crashy_log = _RUNNING_LOG + (
                "E0101 00:00:03.000000       1 printmode.go:272] "
                "Print mode: run ended with error and no response\n"
            )
            log_file = self._write_log(tmp, crashy_log)
            job = {
                "id": "job-4", "kind": "review", "status": "running",
                "log_file": log_file, "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertIn("printmode.go:272", row["log_tail"])

    # --- stall ----------------------------------------------------------

    def test_running_job_with_fresh_output_file_mtime_is_not_stalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(tmp, "")  # mtime is "now"
            job = {
                "id": "job-fresh", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertIsNone(row["stall"])

    def test_running_job_with_stale_output_file_mtime_is_stalled(self):
        """`os.utime` back-dates the mtime and an explicit future `now=` is
        passed — never `time.sleep` — per the contract's clock-seam
        discipline."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(tmp, "")
            old_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(output_file, (old_ts, old_ts))
            job = {
                "id": "job-stale", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            future_now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
            row = build_job_row(job, now=future_now)

        self.assertIsNotNone(row["stall"])
        self.assertNotIsInstance(row["stall"], bool)
        self.assertIsInstance(row["stall"], str)
        self.assertIn("h", row["stall"])  # a duration string ("1h 0m"), not a boolean

    def test_running_job_with_no_output_file_falls_back_to_created_at_for_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            job = {
                "id": "job-no-output", "kind": "review", "status": "running",
                "log_file": log_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            future_now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
            row = build_job_row(job, now=future_now)

        self.assertIsNotNone(row["stall"])

    def test_completed_job_is_never_reported_as_stalled_regardless_of_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(
                tmp, _result_event_ndjson("SUCCESS", response="ok")
            )
            old_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(output_file, (old_ts, old_ts))
            job = {
                "id": "job-done-stale", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            future_now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
            row = build_job_row(job, now=future_now)

        self.assertEqual(row["status"], STATUS_COMPLETED)
        self.assertIsNone(row["stall"])

    # --- usage ------------------------------------------------------------

    def test_result_events_usage_dict_passes_through_to_the_row_unchanged(self):
        usage = {
            "input_tokens": 10, "output_tokens": 2, "thinking_tokens": 0,
            "cache_read_tokens": 0, "total_tokens": 12,
        }
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(
                tmp, _result_event_ndjson("SUCCESS", response="ok", usage=usage)
            )
            job = {
                "id": "job-usage", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(row["usage"], usage)

    def test_running_job_with_no_result_event_has_usage_none(self):
        """No **result event** landed yet -> `usage` is `None`, never
        synthesized zeros (the contract's 'Established facts')."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            job = {
                "id": "job-running-usage", "kind": "review", "status": "running",
                "log_file": log_file, "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertIsNone(row["usage"])

    def test_real_fixture_output_file_carries_the_genuine_usage_shape(self):
        """Genuine captured bytes, exercising the real `usage` shape
        end-to-end — the same discipline used for the other real-fixture
        coverage."""
        with tempfile.TemporaryDirectory() as tmp:
            log_file = self._write_log(tmp, _RUNNING_LOG)
            output_file = self._write_output(tmp, _FIXTURE.read_text(encoding="utf-8"))
            job = {
                "id": "job-fixture-usage", "kind": "review", "status": "running",
                "log_file": log_file, "output_file": output_file,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            row = build_job_row(job)

        self.assertEqual(
            row["usage"],
            {
                "input_tokens": 12308, "output_tokens": 1435, "thinking_tokens": 1219,
                "cache_read_tokens": 0, "total_tokens": 13743,
            },
        )


class RenderStatusTableTest(unittest.TestCase):
    def test_empty_rows_renders_a_no_jobs_message_not_an_empty_table(self):
        rendered = render_status_table([])
        self.assertIn("No agy jobs", rendered)
        self.assertNotIn("|", rendered)

    def test_rows_render_as_a_markdown_table_with_the_expected_columns(self):
        rows = [{
            "id": "job-1", "kind": "review", "status": STATUS_RUNNING,
            "conversation": _BOUND_UUID, "elapsed": "5s", "log_tail": "resolving agent",
        }]
        rendered = render_status_table(rows)

        self.assertIn("job-1", rendered)
        self.assertIn("review", rendered)
        self.assertIn(STATUS_RUNNING, rendered)
        self.assertIn(_BOUND_UUID, rendered)
        self.assertIn("5s", rendered)
        self.assertIn("resolving agent", rendered)

    def test_missing_conversation_renders_a_placeholder_not_a_blank_cell(self):
        rows = [{
            "id": "job-1", "kind": "review", "status": STATUS_RUNNING,
            "conversation": None, "elapsed": "1s", "log_tail": "",
        }]
        rendered = render_status_table(rows)
        self.assertIn("(none yet)", rendered)

    def test_stall_and_usage_columns_render_with_values(self):
        rows = [{
            "id": "job-1", "kind": "review", "status": STATUS_RUNNING,
            "conversation": _BOUND_UUID, "elapsed": "12m 0s", "log_tail": "resolving agent",
            "stall": "12m 0s",
            "usage": {
                "input_tokens": 220, "output_tokens": 4, "thinking_tokens": 0,
                "cache_read_tokens": 0, "total_tokens": 224,
            },
        }]
        rendered = render_status_table(rows)

        self.assertIn("Stall", rendered)
        self.assertIn("Usage", rendered)
        self.assertIn("in:220 out:4 think:0 cache:0 total:224", rendered)

    def test_missing_stall_and_usage_render_the_none_yet_placeholder(self):
        rows = [{
            "id": "job-1", "kind": "review", "status": STATUS_RUNNING,
            "conversation": None, "elapsed": "1s", "log_tail": "",
            "stall": None, "usage": None,
        }]
        rendered = render_status_table(rows)

        # (none yet) covers conversation, stall, and usage — three cells.
        self.assertEqual(rendered.count("(none yet)"), 3)

    def test_terminal_job_with_no_stall_renders_a_distinct_placeholder(self):
        """A terminal **job** (`stall` is structurally `None` per
        `_compute_stall`) must NOT render the same `"(none yet)"` placeholder
        a healthy running job uses — that phrasing implies "still being
        monitored," which is false once the job is terminal."""
        for status in (
            STATUS_COMPLETED, STATUS_CRASHED, STATUS_SILENT_FALLBACK, STATUS_CANCELLED,
        ):
            with self.subTest(status=status):
                rows = [{
                    "id": "job-1", "kind": "review", "status": status,
                    "conversation": _BOUND_UUID, "elapsed": "1s", "log_tail": "",
                    "stall": None, "usage": None,
                }]
                rendered = render_status_table(rows)
                stall_cell = rendered.splitlines()[2].split("|")[6].strip()
                self.assertNotEqual(stall_cell, "(none yet)")
                self.assertTrue(stall_cell)

    def test_running_job_with_no_stall_still_renders_none_yet(self):
        """The running-and-healthy case is unchanged by the terminal fix."""
        rows = [{
            "id": "job-1", "kind": "review", "status": STATUS_RUNNING,
            "conversation": _BOUND_UUID, "elapsed": "1s", "log_tail": "",
            "stall": None, "usage": None,
        }]
        rendered = render_status_table(rows)
        stall_cell = rendered.splitlines()[2].split("|")[6].strip()
        self.assertEqual(stall_cell, "(none yet)")


if __name__ == "__main__":
    unittest.main()
