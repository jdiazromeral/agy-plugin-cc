"""Tests for the `delegate` subcommand — /agy:delegate's companion side.

Method note: the silent-fallback-on-bad-conversation resume path
(`ResumeSilentFallbackTest` below) is the single most dangerous behavior in
this integration, per the mission contract, and is deliberately the first
test in this file.

Layers, mirroring test_review_background.py's two-layer pattern for the
background launch:
  - Pure command-vector and resume-resolution/bind-check unit tests — no
    subprocess, no agy.
  - `DelegateLaunchSeamTest` calls `companion.delegate._run_background_delegate`
    directly with a stub `spawn` — no process is ever started.
  - Subprocess integration tests drive the full CLI
    (`agy_companion.py delegate ...`) against the extended fake `agy`
    (`delegate_*` FAKE_AGY_BEHAVIOR values) for the foreground fresh,
    foreground resume (genuine and silent-fallback), and one background
    end-to-end path. The real agy is never invoked.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import delegate, launch, state, stream_events  # noqa: E402

COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
FAKE_AGY_SOURCE = Path(__file__).resolve().parent / "fake_agy.py"

# The real, committed event-stream fixture — parsed via
# stream_events.parse_event_stream, never hand-written NDJSON, per
# AGENTS.md's discipline for tests/fake_agy.py and M2_purpose.md.
_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "stream_events" / "2026-08-02-run1.ndjson"
_FIXTURE_TEXT = _FIXTURE_PATH.read_text(encoding="utf-8")
_FIXTURE_CONVERSATION_ID = "a4425612-2b6c-4e0c-a9b5-e7600418be81"

_PYTHON_DIR = str(Path(sys.executable).resolve().parent)
_GIT_DIR = str(Path(shutil.which("git")).resolve().parent) if shutil.which("git") else ""

_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
}

_INTENDED_CONVERSATION = "66666666-6666-6666-6666-666666666666"


def _install_fake_agy(bin_dir):
    body = FAKE_AGY_SOURCE.read_text(encoding="utf-8")
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    dest = bin_dir / "agy"
    dest.write_text("#!{}\n".format(sys.executable) + "".join(lines), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def _repo_env():
    env = dict(os.environ)
    env.update(_GIT_ENV)
    return env


def _git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args), cwd=str(repo), env=_repo_env(), capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError("git {} failed: {}".format(" ".join(args), result.stderr))
    return result.stdout


def _dirty_repo(tmp):
    repo = Path(tmp) / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "seed")
    return repo


def _run_delegate(repo, bin_dir, behavior, plugin_data, extra_args=(), extra_env=None):
    env = _repo_env()
    env["PATH"] = os.pathsep.join([str(bin_dir), _GIT_DIR, _PYTHON_DIR])
    env["FAKE_AGY_BEHAVIOR"] = behavior
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(COMPANION), "delegate"] + list(extra_args),
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class _ScopedPluginData:
    """Scopes $CLAUDE_PLUGIN_DATA (for state.list_jobs calls made directly
    from THIS process, as opposed to a subprocess's own env) to a fresh temp
    dir, restoring whatever was there before."""

    def __enter__(self):
        self._had_env = state.PLUGIN_DATA_ENV in os.environ
        self._old_env = os.environ.get(state.PLUGIN_DATA_ENV)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ[state.PLUGIN_DATA_ENV] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc_info):
        if self._had_env:
            os.environ[state.PLUGIN_DATA_ENV] = self._old_env
        else:
            os.environ.pop(state.PLUGIN_DATA_ENV, None)
        self._tmp.cleanup()


# --- the single most dangerous path: resume silently falls back ------------


class ResumeSilentFallbackTest(unittest.TestCase):
    """`--conversation <uuid>` genuinely resumes when known, but silently
    falls back to a brand-new conversation when unknown: exit 0, clean
    stdout, empty stderr — the ONLY trace is in the --log-file. A resume
    must never render this as success, and the wrong new conversation it
    created must never be recorded as this repo's delegate conversation."""

    def test_silent_fallback_on_resume_is_a_distinct_error_not_a_rendered_success(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            # Seed a prior delegate job so --resume has something to resolve.
            first = _run_delegate(
                repo, bin_dir, "delegate_fresh_bound", plugin_data,
                extra_args=["do the first thing"],
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            result = _run_delegate(
                repo, bin_dir, "delegate_resume_silent_fallback", plugin_data,
                extra_args=["--resume", "keep going"],
            )

            jobs = state.list_jobs(str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("fallback", result.stderr.lower())
        # The fake's ordinary-looking stdout must never be rendered as a
        # successful resume.
        self.assertNotIn("agy delegate output", result.stdout)

        delegate_jobs = [j for j in jobs if j["kind"] == "delegate"]
        # The silent-fallback job's wrong new conversation must never be
        # recorded as a successful delegate conversation.
        self.assertIsNone(delegate_jobs[0]["conversation"])


class GenuineResumeTest(unittest.TestCase):
    def test_genuine_resume_binds_the_intended_conversation_and_forwards_output(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            first = _run_delegate(
                repo, bin_dir, "delegate_fresh_bound", plugin_data,
                extra_args=["do the first thing"],
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            result = _run_delegate(
                repo, bin_dir, "delegate_resume_bound", plugin_data,
                extra_args=["--resume", "keep going"],
            )

            jobs = state.list_jobs(str(repo))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agy delegate output", result.stdout)

        delegate_jobs = [j for j in jobs if j["kind"] == "delegate"]
        # delegate_resume_bound echoes back whatever --conversation it was
        # given as the completion marker's uuid — the fresh run's uuid.
        self.assertIsNotNone(delegate_jobs[0]["conversation"])
        self.assertEqual(delegate_jobs[0]["conversation"], delegate_jobs[1]["conversation"])


class ResumeWithNoPriorConversationTest(unittest.TestCase):
    def test_resume_with_no_prior_delegate_job_is_a_clean_error_not_a_silent_fresh_run(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_delegate(
                repo, bin_dir, "delegate_fresh_bound", plugin_data,
                extra_args=["--resume", "keep going"],
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("no prior delegate conversation", result.stderr.lower())
        # Never a silent fresh run — the fake would have written a
        # "delegate output" line if it had actually been invoked.
        self.assertNotIn("agy delegate output", result.stdout)


# --- fresh foreground: exact command vector + persistent job -------------


class FreshForegroundTest(unittest.TestCase):
    def test_fresh_run_forwards_stdout_verbatim_and_records_a_persistent_job(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_delegate(
                repo, bin_dir, "delegate_fresh_bound", plugin_data,
                extra_args=["write a poem"],
            )

            jobs = state.list_jobs(str(repo))
            state_dir = state.resolve_state_dir(str(repo))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "agy delegate output: task complete.\n")

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["kind"], "delegate")
        self.assertIsNotNone(job["conversation"])
        # Persistent, not an ephemeral temp path (unlike review's foreground
        # path) — must live under this repo's state dir so a later --resume
        # can recover it.
        self.assertIn(str(state_dir), job["log_file"])


class MissingAgyBinaryTest(unittest.TestCase):
    def test_missing_agy_binary_is_a_clean_error_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            empty_bin = Path(tmp) / "empty"
            empty_bin.mkdir()

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(empty_bin), _GIT_DIR])
            env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
            result = subprocess.run(
                [sys.executable, str(COMPANION), "delegate", "do something"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("agy", result.stderr.lower())

    def test_launch_failure_still_records_a_job_visible_to_status(self):
        """Before this fix, `_run_live_delegate` computed `job_id`/`log_file`
        but only ever called `state.upsert_job` AFTER `_launch_agy` returned
        successfully — so a `DelegateLaunchError` (missing binary, or a real
        subprocess timeout, which raises the exact same exception at the
        exact same call site) left NO job record at all: the attempt
        vanished, invisible to /agy:status and /agy:result alike, even
        though a timeout means real time (and for a real `agy`, real quota)
        was spent. Covers the shared `except DelegateLaunchError` path — a
        missing binary is the deterministic way to reach it without a real
        subprocess timeout race."""
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            empty_bin = Path(tmp) / "empty"
            empty_bin.mkdir()

            env = _repo_env()
            env["PATH"] = os.pathsep.join([str(empty_bin), _GIT_DIR])
            env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
            result = subprocess.run(
                [sys.executable, str(COMPANION), "delegate", "do something"],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            jobs = state.list_jobs(str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["kind"], "delegate")
        self.assertTrue(job["status"].startswith("error:"), job["status"])
        self.assertIsNone(job.get("output_file"))


# --- pure unit tests: command vector, resume resolution, bind check ------


class CommandVectorTest(unittest.TestCase):
    def test_fresh_vector_matches_the_contract_exactly(self):
        cmd = delegate._agy_command("do the task", "/tmp/x.log")
        self.assertEqual(cmd, [
            "agy", "-p", "do the task",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--sandbox",
            "--new-project",
            "--log-file", "/tmp/x.log",
        ])

    def test_fresh_vector_never_carries_agent_or_conversation_flags(self):
        cmd = delegate._agy_command("do the task", "/tmp/x.log")
        self.assertNotIn("--agent", cmd)
        self.assertNotIn("--conversation", cmd)

    def test_resume_vector_replaces_new_project_with_conversation_at_the_same_position(self):
        cmd = delegate._agy_command(
            "do the task", "/tmp/x.log", conversation="abc-123"
        )
        self.assertEqual(cmd, [
            "agy", "-p", "do the task",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--sandbox",
            "--conversation", "abc-123",
            "--log-file", "/tmp/x.log",
        ])
        self.assertNotIn("--new-project", cmd)

    def test_model_and_effort_are_only_appended_when_supplied(self):
        cmd = delegate._agy_command("do the task", "/tmp/x.log")
        self.assertNotIn("--model", cmd)
        self.assertNotIn("--effort", cmd)

        cmd = delegate._agy_command(
            "do the task", "/tmp/x.log", model="gemini-pro", effort="high"
        )
        self.assertIn("--model", cmd)
        self.assertIn("gemini-pro", cmd)
        self.assertIn("--effort", cmd)
        self.assertIn("high", cmd)


class ResumeCommandVectorTest(unittest.TestCase):
    """`_resume_agy_command` is the seam `_run_live_delegate` calls instead
    of `_agy_command` directly. `_agy_command` itself is untouched — a BARE
    call to it (as these tests make on the right-hand side) still omits
    both `--print-timeout` and `--output-format stream-json`; the wrapper
    is what adds them, always deriving `--print-timeout` from the
    subprocess timeout this same launch will run under
    (`companion.launch._print_timeout_arg`, default `_AGY_TIMEOUT_SECONDS`
    when `timeout` is not passed)."""

    def test_fresh_vector_is_the_bare_agy_command_vector_plus_the_default_print_timeout(self):
        self.assertEqual(
            delegate._resume_agy_command("do the task", "/tmp/x.log"),
            delegate._agy_command(
                "do the task", "/tmp/x.log",
                print_timeout=launch._print_timeout_arg(delegate._AGY_TIMEOUT_SECONDS),
            ),
        )

    def test_resume_vector_adds_output_format_stream_json_after_the_agy_command_vector(self):
        cmd = delegate._resume_agy_command("do the task", "/tmp/x.log", conversation="abc-123")
        self.assertEqual(
            cmd,
            delegate._agy_command(
                "do the task", "/tmp/x.log", conversation="abc-123",
                print_timeout=launch._print_timeout_arg(delegate._AGY_TIMEOUT_SECONDS),
            )
            + ["--output-format", "stream-json"],
        )

    def test_a_custom_timeout_moves_the_derived_print_timeout_with_it(self):
        cmd = delegate._resume_agy_command("do the task", "/tmp/x.log", timeout=45)
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(cmd[cmd.index("--print-timeout") + 1], "35s")


class BackgroundCommandVectorTest(unittest.TestCase):
    """`_background_agy_command` is the seam `_run_background_delegate`
    calls. A **background launch**'s stdout is the ONLY thing
    `/agy:status` and `/agy:result` can read, and both parse it as an
    **event stream** — so `--output-format stream-json` is unconditional
    here, unlike the foreground path where only a resume needs it. Its
    `--print-timeout` is likewise unconditional, and always the large
    background constant (`companion.launch._BACKGROUND_PRINT_TIMEOUT_ARG`),
    never derived from any subprocess timeout — a background launch has no
    timeout ceiling by design (see `run()`'s `--timeout` + `--background`
    rejection)."""

    def test_fresh_background_vector_requests_the_event_stream(self):
        self.assertEqual(
            delegate._background_agy_command("do the task", "/tmp/x.log"),
            delegate._agy_command(
                "do the task", "/tmp/x.log",
                print_timeout=launch._BACKGROUND_PRINT_TIMEOUT_ARG,
            )
            + ["--output-format", "stream-json"],
        )

    def test_resume_background_vector_requests_the_event_stream_too(self):
        self.assertEqual(
            delegate._background_agy_command("do the task", "/tmp/x.log", conversation="abc-123"),
            delegate._agy_command(
                "do the task", "/tmp/x.log", conversation="abc-123",
                print_timeout=launch._BACKGROUND_PRINT_TIMEOUT_ARG,
            )
            + ["--output-format", "stream-json"],
        )

    def test_background_vector_carries_the_large_explicit_value_not_agys_5m_default(self):
        cmd = delegate._background_agy_command("do the task", "/tmp/x.log")
        self.assertIn("--print-timeout", cmd)
        self.assertEqual(cmd[cmd.index("--print-timeout") + 1], "24h")


class BindCheckTest(unittest.TestCase):
    """Exercises the structural identity comparison against the real,
    committed event-stream fixture — never hand-written NDJSON, per the
    contract and M2_purpose.md's discipline."""

    def setUp(self):
        self.event_stream = stream_events.parse_event_stream(_FIXTURE_TEXT)

    def test_matching_result_event_conversation_id_confirms_the_bind(self):
        bound, proof = delegate._resume_bind_check(self.event_stream, _FIXTURE_CONVERSATION_ID)
        self.assertTrue(bound)
        self.assertEqual(proof, _FIXTURE_CONVERSATION_ID)

    def test_a_different_intended_uuid_than_the_result_events_is_not_a_confirmed_bind(self):
        """The event stream itself reports SUCCESS (agy thinks it worked) on
        a conversation_id that does not match what was requested — the
        silent-fallback signature this mission's bind check must catch."""
        bound, proof = delegate._resume_bind_check(self.event_stream, _INTENDED_CONVERSATION)
        self.assertFalse(bound)
        self.assertEqual(proof, _FIXTURE_CONVERSATION_ID)

    def test_a_stream_that_never_reaches_a_result_event_fails_closed(self):
        """Slice the real fixture bytes (drop its last line, the result
        event) rather than hand-write NDJSON, per the contract's
        fail-closed test-case allowance."""
        truncated_text = "\n".join(_FIXTURE_TEXT.splitlines()[:-1])
        truncated_stream = stream_events.parse_event_stream(truncated_text)
        bound, proof = delegate._resume_bind_check(truncated_stream, _FIXTURE_CONVERSATION_ID)
        self.assertFalse(bound)

    def test_an_unparseable_event_stream_fails_closed(self):
        """`None` (what `_parse_event_stream_safely` returns on a parse
        failure) is never a confirmed bind — "cannot establish" is not a
        pass."""
        bound, proof = delegate._resume_bind_check(None, _FIXTURE_CONVERSATION_ID)
        self.assertFalse(bound)


class ResolveLastConversationTest(unittest.TestCase):
    def setUp(self):
        self._scope = _ScopedPluginData()
        self._plugin_data = self._scope.__enter__()
        self.addCleanup(lambda: self._scope.__exit__(None, None, None))

    def test_no_delegate_job_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir) / "repo"
            repo_root.mkdir()
            self.assertIsNone(delegate._resolve_last_conversation(str(repo_root)))

    def test_prefers_the_stored_conversation_field_over_reparsing_the_log(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir) / "repo"
            repo_root.mkdir()
            state.upsert_job(str(repo_root), {
                "id": "delegate-1", "kind": "delegate", "status": "completed",
                "log_file": None, "conversation": "stored-uuid",
            })
            self.assertEqual(
                delegate._resolve_last_conversation(str(repo_root)), "stored-uuid"
            )

    def test_only_considers_the_single_most_recent_delegate_job(self):
        """Per the contract's design note: resolution reads the MOST RECENT
        delegate job's log, not the whole history — a broken resume attempt
        does not fall through to an older, still-valid conversation."""
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir) / "repo"
            repo_root.mkdir()
            state.upsert_job(str(repo_root), {
                "id": "delegate-older", "kind": "delegate", "status": "completed",
                "log_file": None, "conversation": "older-uuid",
            })
            # A newer delegate job whose resume silently fell back — no
            # stored conversation, and its log (if any) would show the
            # "not found" trace. Simulate via a job with no log at all.
            state.upsert_job(str(repo_root), {
                "id": "delegate-newer", "kind": "delegate",
                "status": "error: silent fallback", "log_file": None,
                "conversation": None,
            })
            self.assertIsNone(delegate._resolve_last_conversation(str(repo_root)))

    def test_non_delegate_jobs_are_skipped(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir) / "repo"
            repo_root.mkdir()
            state.upsert_job(str(repo_root), {
                "id": "review-1", "kind": "review", "status": "completed",
                "log_file": None, "conversation": None,
            })
            state.upsert_job(str(repo_root), {
                "id": "delegate-1", "kind": "delegate", "status": "completed",
                "log_file": None, "conversation": "the-uuid",
            })
            self.assertEqual(
                delegate._resolve_last_conversation(str(repo_root)), "the-uuid"
            )


# --- background launch: two-layer pattern, mirroring test_review_background.py --


def _wait_for_marker(log_path, needle, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                return text
        time.sleep(interval)
    return text


class DelegateLaunchSeamTest(unittest.TestCase):
    """No subprocess, no real agy — `spawn` is a stub the test controls."""

    def setUp(self):
        self._scope = _ScopedPluginData()
        self._scope.__enter__()
        self.addCleanup(lambda: self._scope.__exit__(None, None, None))

    def test_background_launch_writes_a_running_delegate_job_without_waiting(self):
        calls = []

        class _FakeProc:
            pid = 4242

        def stub_spawn(cmd, cwd, stdout_path=None):
            calls.append((cmd, cwd, stdout_path))
            return _FakeProc()  # never awaited — the seam does not block on it.

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            exit_code = delegate._run_background_delegate(
                str(repo_root), "do the task", None, None, None, spawn=stub_spawn
            )
            jobs = state.list_jobs(str(repo_root))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        cmd, cwd, stdout_path = calls[0]
        self.assertEqual(cwd, str(repo_root))
        self.assertNotIn("--agent", cmd)
        self.assertIn("--new-project", cmd)
        # The detached run's captured stdout is the only signal /agy:status
        # and /agy:result have, and both read it as an **event stream**.
        self.assertEqual(cmd[-2:], ["--output-format", "stream-json"])

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["kind"], "delegate")
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["conversation"])
        self.assertEqual(job["pid"], 4242)
        # The job's stdout must be captured to a persistent output file (the
        # detached agy's stdout is the stored **result** — without this it
        # is lost to DEVNULL for good), mirroring review.py's background
        # launch (see _run_background_delegate's looper comment).
        self.assertEqual(job["output_file"], stdout_path)
        self.assertIsNotNone(stdout_path)

    def test_background_resume_launch_never_records_an_unverified_conversation(self):
        """Even a resume's intended uuid is not written into the job record
        at launch time — nothing has bind-checked it yet."""
        def stub_spawn(cmd, cwd, stdout_path=None):
            return None

        with tempfile.TemporaryDirectory() as repo:
            repo_root = Path(repo) / "my-repo"
            repo_root.mkdir()
            delegate._run_background_delegate(
                str(repo_root), "keep going", None, None, "some-uuid", spawn=stub_spawn
            )
            jobs = state.list_jobs(str(repo_root))

        self.assertIsNone(jobs[0]["conversation"])


class DelegateBackgroundSubprocessTest(unittest.TestCase):
    def test_background_delegate_returns_immediately_and_prints_a_job_id(self):
        with tempfile.TemporaryDirectory() as tmp, _ScopedPluginData() as plugin_data:
            repo = _dirty_repo(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            _install_fake_agy(bin_dir)

            result = _run_delegate(
                repo, bin_dir, "delegate_background_finished", plugin_data,
                extra_args=["--background", "do the task"],
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("job", result.stdout.lower())

            jobs = state.list_jobs(str(repo))
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job["kind"], "delegate")
            self.assertIn(job["id"], result.stdout)

            log_text = _wait_for_marker(Path(job["log_file"]), "clearing ResponsePending")
            self.assertIn("Stream completed for", log_text)

            # The detached delegate's stdout must be captured to a
            # persistent output_file so /agy:result can later harvest it —
            # see _run_background_delegate's looper comment for the M6/M7
            # parallel-fork gap this closes.
            self.assertIsNotNone(job.get("output_file"))
            output_text = _wait_for_marker(Path(job["output_file"]), "task complete.")
            self.assertIn("agy delegate output: task complete.", output_text)


if __name__ == "__main__":
    unittest.main()
