"""Unit tests for Claude Code lifecycle hooks (session_start, session_end)."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "plugins" / "agy" / "scripts" / "hooks"
SESSION_HOOK_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "session_hook"
sys.path.insert(0, str(HOOKS_DIR))

import session_end  # noqa: E402
import session_start  # noqa: E402


class SessionStartHookTest(unittest.TestCase):
    def test_session_start_appends_env_var_to_claude_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = pathlib.Path(tmp) / "claude.env"
            orig_env = os.environ.get("CLAUDE_ENV_FILE")
            try:
                os.environ["CLAUDE_ENV_FILE"] = str(env_file)
                # Simulate stdin json
                import io
                old_stdin = sys.stdin
                sys.stdin = io.StringIO(json.dumps({"session_id": "test-session-1234"}))
                try:
                    ret = session_start.main()
                finally:
                    sys.stdin = old_stdin

                self.assertEqual(ret, 0)
                content = env_file.read_text(encoding="utf-8")
                self.assertIn('export AGY_COMPANION_SESSION_ID="test-session-1234"', content)
            finally:
                if orig_env is None:
                    os.environ.pop("CLAUDE_ENV_FILE", None)
                else:
                    os.environ["CLAUDE_ENV_FILE"] = orig_env


class SessionEndHookTest(unittest.TestCase):
    def test_session_end_never_signals_a_live_process_and_never_mutates_state_json(self):
        """Replaces test_session_end_cancels_matching_running_jobs, which
        asserted the opposite, buggy behavior. Proven over GENUINELY alive
        processes (never an already-gone pid, which would prove nothing):
        SessionEnd must not signal either job's process, and must not
        rewrite state.json's `jobs` array at all — not the ending session's
        own job, and not any other job either.
        """
        target_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        other_proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_dir = pathlib.Path(tmp) / "state" / "myrepo-abc123"
                state_dir.mkdir(parents=True)
                state_file = state_dir / "state.json"
                initial_state = {
                    "version": 1,
                    "jobs": [
                        {
                            "id": "job-1",
                            "session_id": "sess-target",
                            "status": "running",
                            "pid": target_proc.pid,
                        },
                        {
                            "id": "job-2",
                            "session_id": "sess-other",
                            "status": "running",
                            "pid": other_proc.pid,
                        },
                    ],
                }
                original_json = json.dumps(initial_state)
                state_file.write_text(original_json, encoding="utf-8")

                orig_plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
                try:
                    os.environ["CLAUDE_PLUGIN_DATA"] = tmp
                    session_end.cleanup_session_jobs("sess-target")
                finally:
                    if orig_plugin_data is None:
                        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
                    else:
                        os.environ["CLAUDE_PLUGIN_DATA"] = orig_plugin_data

                # Both processes must still be alive — SessionEnd must never
                # signal a job's process, targeted or not.
                self.assertIsNone(
                    target_proc.poll(),
                    "BUG REPRODUCED: SessionEnd signalled the ending session's own job",
                )
                self.assertIsNone(
                    other_proc.poll(),
                    "BUG REPRODUCED: SessionEnd signalled a job outside the ending session",
                )

                # state.json must be untouched — no read-modify-write at all.
                self.assertEqual(
                    state_file.read_text(encoding="utf-8"),
                    original_json,
                    "BUG REPRODUCED: SessionEnd mutated state.json",
                )
        finally:
            target_proc.kill()
            other_proc.kill()
            target_proc.wait(timeout=5)
            other_proc.wait(timeout=5)


def _load_events(name):
    """Every parsed NDJSON event of one committed `session_hook` capture.
    Duplicated per test module on purpose — see AGENTS.md's "Duplicate small
    test helpers per file; never import across `tests.*`"."""
    path = SESSION_HOOK_FIXTURES / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _system_event(events, subtype):
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == subtype:
            return event
    return None


def _bash_commands_and_results(events):
    commands, results = [], []
    for event in events:
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                commands.append((block.get("input") or {}).get("command") or "")
            elif block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                results.append(content or "")
    return commands, results


class SessionHookLiveCaptureFixtureTest(unittest.TestCase):
    """Holds the committed `tests/fixtures/session_hook/` bytes — real
    `claude -p --output-format stream-json` output captured by
    `tools/live_session_hook_capture.py` — against the two hypotheses the
    `modernize-127` epic named. See that directory's PROVENANCE.md.

    These never run `claude` or `agy`; they parse committed bytes, the same
    way `tests/test_log_fidelity.py` holds regexes against real agy logs.
    """

    PROBE1 = "2026-09-19-probe1-agy-status.stream.ndjson"
    PROBE2 = "2026-09-19-probe2-env-value.stream.ndjson"

    def test_provenance_records_which_plugin_directory_the_capture_loaded(self):
        self.assertTrue((SESSION_HOOK_FIXTURES / "PROVENANCE.md").is_file())

    def test_session_start_hook_actually_ran_and_exited_zero(self):
        """Refutes hypothesis (a) — "the hooks never ran, because Claude Code
        loaded the plugin's hook configuration from the stale 0.2.0
        marketplace cache, which has no hooks/ directory at all"."""
        for name in (self.PROBE1, self.PROBE2):
            events = _load_events(name)
            response = _system_event(events, "hook_response")
            self.assertIsNotNone(response, "{}: no hook_response event at all".format(name))
            self.assertEqual(response["hook_event"], "SessionStart")
            self.assertEqual(response["exit_code"], 0)
            self.assertEqual(response["outcome"], "success")
            self.assertEqual(response["stderr"], "")
            # Exactly one SessionStart hook ran, which is what lets the
            # PROVENANCE attribute it to this plugin.
            started = [
                e for e in events
                if e.get("type") == "system" and e.get("subtype") == "hook_started"
            ]
            self.assertEqual(len(started), 1, "{}: expected one hook_started".format(name))

    def test_capture_loaded_the_agy_plugin_from_the_directory_marketplace_source(self):
        """The loaded directory is read off the `init` event, never assumed —
        and it is the live 0.3.0 source, not the stale 0.2.0 install cache."""
        for name in (self.PROBE1, self.PROBE2):
            init = _system_event(_load_events(name), "init")
            self.assertIsNotNone(init, "{}: no init event".format(name))
            agy = [p for p in init.get("plugins") or [] if p.get("name") == "agy"]
            self.assertEqual(len(agy), 1, "{}: expected exactly one agy plugin".format(name))
            self.assertEqual(
                agy[0]["path"],
                "/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy",
            )
            self.assertEqual(agy[0]["version"], "0.3.0")
            self.assertNotIn("plugins/cache/agy", agy[0]["path"])

    def test_env_var_reaches_a_subprocess_with_the_exact_pinned_session_id(self):
        """Refutes hypothesis (b) — "the hooks ran ... but the exported
        variable does not reach a companion subprocess". Asserts the exact
        value, not merely that something was set."""
        events = _load_events(self.PROBE2)
        session_id = _system_event(events, "init")["session_id"]
        _, results = _bash_commands_and_results(events)
        self.assertIn(
            "MEASURED_ENV AGY_COMPANION_SESSION_ID='{}'".format(session_id),
            "\n".join(results),
        )

    def test_agy_status_companion_saw_the_session_and_scoped_to_it(self):
        """The production invocation (`/agy:status`) against a **state dir**
        seeded with one matching and one sentinel **job**. Asserts what must
        be ABSENT as well as present — per AGENTS.md, a row-presence-only
        assertion would pass just as happily against an unscoped table."""
        events = _load_events(self.PROBE1)
        commands, results = _bash_commands_and_results(events)
        table = "\n".join(results)
        self.assertEqual(
            commands,
            [
                'python3 "/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc'
                '/plugins/agy/scripts/agy_companion.py" status'
            ],
        )
        self.assertIn("seeded-match-job", table)
        self.assertNotIn("seeded-other-job", table)
        self.assertNotIn("No agy jobs", table)
        # The sentinel job really was seeded — otherwise its absence above
        # would prove nothing.
        seeded = json.loads(
            (SESSION_HOOK_FIXTURES / "2026-09-19-probe1-seeded-state.json").read_text(
                encoding="utf-8"
            )
        )
        by_id = {job["id"]: job for job in seeded["jobs"]}
        self.assertEqual(by_id["seeded-match-job"]["session_id"], events[0]["session_id"])
        self.assertEqual(
            by_id["seeded-other-job"]["session_id"], "SENTINEL-NOT-THE-CAPTURED-SESSION"
        )

    def test_no_captured_session_ever_invoked_agy(self):
        """The `tool_use` blocks are the complete list of commands the
        captured sessions ran; this mission spends no agy quota."""
        for name in (self.PROBE1, self.PROBE2):
            commands, _ = _bash_commands_and_results(_load_events(name))
            self.assertTrue(commands, "{}: no commands captured at all".format(name))
            for command in commands:
                self.assertTrue(
                    command.startswith("python3 "),
                    "{}: captured a non-python3 command: {!r}".format(name, command),
                )
                self.assertNotIn("agy -p", command)


if __name__ == "__main__":
    unittest.main()
