"""Tests for companion.result — the `result` subcommand that harvests a
finished **job**'s stored final output (its **result**).

Driven directly over `companion.result.result_for_job` with job records
and their `log_file`/`output_file` written to a temp state dir — no
subprocess, no real agy, no wall-clock timing races.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import result, state  # noqa: E402

_BOUND_UUID = "22222222-2222-2222-2222-222222222222"
_RUNNING_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    "I0101 00:00:00.000000       1 server.go:934] Created conversation {}\n"
).format(_BOUND_UUID)
_SILENT_FALLBACK_LOG = (
    "I0101 00:00:00.000000       1 printmode.go:108] resolving agent\n"
    'W0101 00:00:00.000000       1 printmode.go:166] Agent "agy-review" not found, '
    "falling back to default\n"
)

_VALID_REVIEW_JSON = (
    '{"findings": [{"title": "Off-by-one", "body": "b", "confidence_score": 0.8, '
    '"code_location": {"absolute_file_path": "a.py", "line_range": {"start": 1, "end": 2}}, '
    '"priority": 1}], "overall_correctness": "patch is incorrect", '
    '"overall_explanation": "one finding", "overall_confidence_score": 0.7}'
)

_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "stream_events" / "2026-08-02-run1.ndjson"
)


def _result_event_ndjson(response=None, status="SUCCESS", conversation_id=_BOUND_UUID, error=None):
    """A minimal, synthetic **event stream** — one **result event** line,
    built with `json.dumps` against the field names `stream_events.py`'s
    own dataclasses use. NOT a claimed real trace — see
    `tests/fixtures/stream_events/PROVENANCE.md` for the genuine captured
    one, used verbatim in ResultForJobFixtureTest below."""
    result = {
        "conversation_id": conversation_id,
        "status": status,
        "response": response,
    }
    if error is not None:
        result["error"] = error
    return json.dumps({"event": "result", "result": result}) + "\n"


class _StateDirCase(unittest.TestCase):
    def setUp(self):
        self._plugin_data = tempfile.TemporaryDirectory()
        self.addCleanup(self._plugin_data.cleanup)
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        os.environ[state.PLUGIN_DATA_ENV] = self._plugin_data.name
        self.addCleanup(self._restore_env)

        self._repo_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._repo_dir.cleanup)
        self.repo_root = str(Path(self._repo_dir.name) / "my-repo")
        Path(self.repo_root).mkdir()

    def _restore_env(self):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)

    def _write_job(self, job_id, log_text, output_text=None, kind="review", **patch):
        log_file = state.resolve_job_log_file(self.repo_root, job_id)
        log_file.write_text(log_text, encoding="utf-8")
        record = {"id": job_id, "kind": kind, "status": "running", "log_file": str(log_file)}
        if output_text is not None:
            output_file = state.resolve_job_output_file(self.repo_root, job_id)
            output_file.write_text(output_text, encoding="utf-8")
            record["output_file"] = str(output_file)
        record.update(patch)
        state.upsert_job(self.repo_root, record)
        return record


class ResultForJobStillActiveTest(_StateDirCase):
    """A job that is still running, silently fell back, or crashed is
    reported as that state — never rendered as an empty clean review."""

    def test_running_job_is_reported_as_running_not_rendered_empty(self):
        job = self._write_job("job-running", _RUNNING_LOG)
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("running", message.lower())
        self.assertIn(job["id"], message)

    def test_silent_fallback_job_is_reported_as_silent_fallback(self):
        job = self._write_job("job-fallback", _SILENT_FALLBACK_LOG)
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("fallback", message.lower())

    def test_crashed_job_is_reported_as_crashed(self):
        """Status "FAILED" is synthetic-for-branch-coverage only — no real
        crash `result.status` value has ever been observed live."""
        job = self._write_job(
            "job-crashed", _RUNNING_LOG,
            output_text=_result_event_ndjson(response=None, status="FAILED"),
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("crashed", message.lower())

    def test_default_selection_with_only_a_running_job_reports_it_running(self):
        """No job-id given, and the only job is still running — must never
        silently render nothing or an empty review."""
        self._write_job("job-running", _RUNNING_LOG)
        exit_code, message = result.result_for_job(self.repo_root, None)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("running", message.lower())


class ResultForJobFinishedTest(_StateDirCase):
    def test_completed_review_job_renders_the_finding_table_and_verdict(self):
        job = self._write_job(
            "job-review", _RUNNING_LOG,
            output_text=_result_event_ndjson(response=_VALID_REVIEW_JSON),
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Off-by-one", message)
        self.assertIn("Verdict: patch is incorrect", message)

    def test_completed_review_job_with_unparseable_response_renders_raw_text_verbatim(self):
        job = self._write_job(
            "job-raw", _RUNNING_LOG,
            output_text=_result_event_ndjson(response="not json at all"),
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "not json at all")

    def test_non_review_kind_renders_raw_harvested_text_verbatim_even_if_json_shaped(self):
        job = self._write_job(
            "job-other", _RUNNING_LOG,
            output_text=_result_event_ndjson(response=_VALID_REVIEW_JSON),
            kind="delegate",
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, _VALID_REVIEW_JSON)

    def test_result_event_present_with_no_response_falls_back_to_raw_output_file_text(self):
        """When a **result event** is present but its `response` is absent,
        the harvest falls back to the raw captured `output_file` text — the
        same fallback a stream that never reached a result event gets."""
        raw = _result_event_ndjson(response=None)
        job = self._write_job("job-no-response", _RUNNING_LOG, output_text=raw, kind="delegate")
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, raw)

    def test_result_event_present_with_empty_string_response_falls_back_to_raw_output_file_text(self):
        """An empty-string `response` (present, but `""`) is distinct from
        an absent one (`None`, covered above) — `EventStream.response is not
        None` alone lets `""` through and _render_result then returns `""`,
        which `run()` would print as a blank line with exit 0. That is the
        empty-review-with-exit-0 bug this test reproduces: must fall back to
        the raw captured `output_file` text exactly like the no-response
        case does."""
        raw = _result_event_ndjson(response="")
        job = self._write_job("job-empty-response", _RUNNING_LOG, output_text=raw, kind="delegate")
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, raw)
        self.assertTrue(message.strip(), "empty response must not render as blank output")

    def test_cancelled_job_with_no_stored_output_reports_that_clearly_not_a_crash(self):
        """Replaces the old "completed job with no stored output" case:
        under the new derivation, STATUS_COMPLETED literally requires an
        `output_file` whose event stream reached a SUCCESS result event, so
        "completed but nothing was captured" is no longer expressible — the
        two facts now share one source. `cancelled` is the one status that
        still short-circuits straight to "finished" regardless of output,
        so it is the realistic way a finished job can have zero captured
        output (cancelled before anything was written)."""
        job = self._write_job("job-cancelled-empty", _RUNNING_LOG, status="cancelled")
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn(job["id"], message)
        self.assertIn("no stored", message.lower())

    def test_cancelled_job_is_treated_as_finished_and_renders_its_stored_output(self):
        job = self._write_job(
            "job-cancelled", _RUNNING_LOG, output_text="partial output", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "partial output")

    def test_errored_foreground_delegate_job_is_treated_as_finished_and_renders_its_stored_output(self):
        """A foreground `delegate` job that exits nonzero stores the bare
        `error` status (delegate.py's `_run_live_delegate`), which
        `status.build_job_row` now trusts as a terminal status (see
        `_is_stored_terminal_status`). Before this fix, `result.py`'s own
        `_FINISHED_STATUSES` allowlist did not include it, so `/agy:result`
        wrongly reported a permanently-finished errored job as "not finished
        yet. Check /agy:status and try again once it finishes" forever."""
        job = self._write_job(
            "job-errored", _RUNNING_LOG, output_text="partial output before the crash",
            status="error",
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "partial output before the crash")

    def test_no_job_id_defaults_to_the_most_recently_finished_job(self):
        self._write_job(
            "job-old-done", _RUNNING_LOG, output_text=_result_event_ndjson(response="old result")
        )
        newest = self._write_job(
            "job-new-done", _RUNNING_LOG, output_text=_result_event_ndjson(response="new result")
        )
        exit_code, message = result.result_for_job(self.repo_root, None)
        self.assertEqual(exit_code, 0)
        self.assertEqual(message, "new result")
        self.assertIsNotNone(newest)

    def test_no_jobs_at_all_is_a_clear_message_not_a_crash(self):
        exit_code, message = result.result_for_job(self.repo_root, None)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("no", message.lower())


class ResultForJobCrashedResultEventTest(_StateDirCase):
    """A **result event** whose `status` is present and not `SUCCESS` means
    the run STOPPED without succeeding (glossary) — even when the *job* as a
    whole is finished (here: a `cancelled` job whose stream nonetheless
    reached its own result event, e.g. killed mid-run). `_render_result`
    must render that `status` and the human-readable `error` string instead
    of falling through to the raw-text/`tolerant_parse` path, for every
    **kind**. All NDJSON here is hand-written/synthetic via
    `_result_event_ndjson`, covering branch combinations (non-review
    `kind`, a generic non-`"ERROR"` crashed `status`, a missing `error`
    string) that a single real capture does not exercise on its own; none
    of it is verification against the real `agy` binary. Real captured
    ERROR-status bytes now exist
    (`tests/fixtures/error_result/2026-08-02-run1.ndjson`,
    the `agy-119` epic, provoked via `--print-timeout`) and are asserted
    against the real shipped rendering path by
    `tests/test_log_fidelity.py`'s `RealErrorResultEventTest` — that real
    capture's shape matched what this synthetic helper already assumed for
    every field `_render_result` actually reads (`status`, `error`); see
    that fixture's `PROVENANCE.md` for the one respect it did NOT match a
    prior assumption (the **usage** block, which `_render_result`'s ERROR
    branch never reads, so nothing here needed to change)."""

    def test_error_status_result_event_renders_status_and_error_for_review_kind(self):
        raw = (
            '{"event": "init", "conversation_id": "' + _BOUND_UUID + '"}\n'
            + _result_event_ndjson(response="", status="ERROR", error="deadline exceeded")
        )
        job = self._write_job(
            "job-error-review", _RUNNING_LOG, output_text=raw, kind="review", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("ERROR", message)
        self.assertIn("deadline exceeded", message)
        # Must be a rendered status/error message, never the raw NDJSON
        # dump — the whole point of this mission is that /agy:result stops
        # printing the event stream's own wire-format keys verbatim.
        self.assertNotIn("conversation_id", message)
        self.assertNotIn('"event"', message)

    def test_error_status_result_event_renders_status_and_error_for_non_review_kind(self):
        """The fallback docstring already says this applies to every job
        kind, not just review — this is the branch-coverage proof."""
        raw = (
            '{"event": "init", "conversation_id": "' + _BOUND_UUID + '"}\n'
            + _result_event_ndjson(response="", status="ERROR", error="deadline exceeded")
        )
        job = self._write_job(
            "job-error-delegate", _RUNNING_LOG, output_text=raw, kind="delegate", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("ERROR", message)
        self.assertIn("deadline exceeded", message)
        self.assertNotIn("conversation_id", message)
        self.assertNotIn('"event"', message)

    def test_non_error_non_success_status_is_treated_generically_not_special_cased(self):
        """Mirrors status.py's own derive_status: "present and not SUCCESS"
        is the generic crashed signal, not a check for the literal string
        "ERROR"."""
        raw = (
            '{"event": "init", "conversation_id": "' + _BOUND_UUID + '"}\n'
            + _result_event_ndjson(response=None, status="FAILED", error="boom")
        )
        job = self._write_job(
            "job-failed-status", _RUNNING_LOG, output_text=raw, kind="delegate", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("FAILED", message)
        self.assertIn("boom", message)
        self.assertNotIn("conversation_id", message)

    def test_missing_error_string_falls_back_to_a_clear_placeholder(self):
        raw = (
            '{"event": "init", "conversation_id": "' + _BOUND_UUID + '"}\n'
            + _result_event_ndjson(response=None, status="ERROR")
        )
        job = self._write_job(
            "job-error-no-message", _RUNNING_LOG, output_text=raw, kind="delegate", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("ERROR", message)
        self.assertNotIn("None", message)
        self.assertNotIn("conversation_id", message)

    def test_success_status_still_uses_the_unchanged_success_path(self):
        """Regression guard: a SUCCESS result event must not be swept into
        the new ERROR branch — the harvested-response / tolerant_parse path
        stays exactly as it was."""
        raw = _result_event_ndjson(response=_VALID_REVIEW_JSON, status="SUCCESS")
        job = self._write_job(
            "job-success-review", _RUNNING_LOG, output_text=raw, kind="review", status="cancelled"
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Off-by-one", message)


class ResultForJobFixtureTest(_StateDirCase):
    """Genuine captured bytes, used verbatim as `output_file` content —
    proving the harvest path parses real agy output end to end, not just
    synthetic NDJSON (per the acceptance criteria)."""

    def test_real_fixture_output_file_renders_the_captured_finding(self):
        job = self._write_job(
            "job-fixture", _RUNNING_LOG, output_text=_FIXTURE.read_text(encoding="utf-8")
        )
        exit_code, message = result.result_for_job(self.repo_root, job["id"])
        self.assertEqual(exit_code, 0)
        self.assertIn(
            "[P1] Use addition instead of subtraction in add function", message
        )
        self.assertIn("Verdict: patch is incorrect", message)


class ResultForJobSelectionErrorsTest(_StateDirCase):
    def test_unknown_job_id_is_a_clear_error(self):
        self._write_job("job-real", _RUNNING_LOG, output_text=_result_event_ndjson(response="r"))
        exit_code, message = result.result_for_job(self.repo_root, "no-such-job")
        self.assertNotEqual(exit_code, 0)
        self.assertIn("no-such-job", message)

    def test_ambiguous_job_id_prefix_is_a_clear_error(self):
        self._write_job(
            "job-abc111", _RUNNING_LOG, output_text=_result_event_ndjson(response="r1")
        )
        self._write_job(
            "job-abc222", _RUNNING_LOG, output_text=_result_event_ndjson(response="r2")
        )
        exit_code, message = result.result_for_job(self.repo_root, "job-abc")
        self.assertNotEqual(exit_code, 0)
        self.assertIn("ambiguous", message.lower())


class ResultCliWiringTest(unittest.TestCase):
    """One subprocess smoke test proving `agy_companion.py result [job-id]`
    is wired end to end through argparse (nargs='?' accepts a bare
    `result` with no job-id, and consumes a job-id positional when given)
    — the underlying selection/rendering logic itself is covered above."""

    def test_result_subcommand_with_no_jobs_exits_nonzero_with_a_clear_message(self):
        had_env = state.PLUGIN_DATA_ENV in os.environ
        old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "repo"
                repo.mkdir()
                subprocess.run(
                    ["git", "init", "--quiet"], cwd=str(repo), check=True, timeout=10
                )
                plugin_data = Path(tmp) / "plugin-data"
                plugin_data.mkdir()
                env = dict(os.environ)
                env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)

                result_proc = subprocess.run(
                    [sys.executable, str(COMPANION), "result"],
                    cwd=str(repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(result_proc.returncode, 0)
                self.assertIn("no agy jobs", result_proc.stdout.lower())
        finally:
            if had_env:
                os.environ[state.PLUGIN_DATA_ENV] = old_env
            else:
                os.environ.pop(state.PLUGIN_DATA_ENV, None)


if __name__ == "__main__":
    unittest.main()
