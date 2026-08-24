"""Behavior tests for the `setup` doctor, driven through the companion's
public interface: invoke it as a subprocess with a fake agy on PATH and
assert on its stdout / --json output. Never invokes the real agy binary.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"
FAKE_AGY_SOURCE = Path(__file__).resolve().parent / "fake_agy.py"
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion import launch  # noqa: E402
from companion import probe as probe_module  # noqa: E402
from companion import setup as setup_module  # noqa: E402

# Dirname(sys.executable) is added to PATH only so the fake agy's
# shebang-less exec path can find its own interpreter; it is never the
# directory holding the real agy binary on this machine, so it cannot leak
# the real agy into a test's PATH.
_PYTHON_DIR = str(Path(sys.executable).resolve().parent)


def _install_fake_agy(bin_dir):
    """Copy fake_agy.py into bin_dir as an executable named `agy`, with the
    shebang pinned to the interpreter running the tests (not /usr/bin/env)
    so it never depends on PATH to locate python3."""
    body = FAKE_AGY_SOURCE.read_text(encoding="utf-8")
    lines = body.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    dest = bin_dir / "agy"
    dest.write_text("#!{}\n".format(sys.executable) + "".join(lines), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def _run_setup(
    bin_dir=None,
    behavior=None,
    extra_args=(),
    bind_probe_behavior=None,
    agy_version=None,
    probe_behavior=None,
    models_behavior=None,
):
    """Run the companion's setup subcommand with a tightly scoped PATH so
    the real agy on this machine can never be found."""
    path_entries = [_PYTHON_DIR]
    if bin_dir is not None:
        path_entries.insert(0, str(bin_dir))
    env = {"PATH": os.pathsep.join(path_entries)}
    if behavior is not None:
        env["FAKE_AGY_BEHAVIOR"] = behavior
    if bind_probe_behavior is not None:
        env["FAKE_AGY_BIND_PROBE_BEHAVIOR"] = bind_probe_behavior
    if agy_version is not None:
        env["FAKE_AGY_VERSION"] = agy_version
    if probe_behavior is not None:
        env["FAKE_AGY_PROBE_BEHAVIOR"] = probe_behavior
    if models_behavior is not None:
        env["FAKE_AGY_MODELS_BEHAVIOR"] = models_behavior
    return subprocess.run(
        [sys.executable, str(COMPANION), "setup"] + list(extra_args),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class SetupDoctorTest(unittest.TestCase):
    def test_reports_agy_version_when_installed_and_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="authenticated_with_agents", extra_args=["--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["agy"]["installed"])
        self.assertEqual(payload["agy"]["version"], "1.1.6")

    def test_reports_registered_agents_when_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="authenticated_with_agents", extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["auth"]["state"], "authenticated")
        self.assertEqual(payload["agents"]["state"], "ok")
        self.assertEqual(payload["agents"]["names"], ["code-auditor"])

    def test_reports_empty_agent_list_distinctly_from_a_listing_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="authenticated_no_agents", extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["agents"]["state"], "ok")
        self.assertEqual(payload["agents"]["names"], [])

    def test_reports_not_authenticated(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="not_authenticated", extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertTrue(payload["agy"]["installed"])
        self.assertEqual(payload["auth"]["state"], "not_authenticated")
        self.assertEqual(payload["agents"]["state"], "error")
        self.assertEqual(payload["agents"]["names"], [])

    def test_reports_unknown_auth_when_agent_listing_fails_for_another_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="agents_list_fails", extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["auth"]["state"], "unknown")
        self.assertEqual(payload["agents"]["state"], "error")

    def test_reports_agy_absent_without_touching_a_real_agy(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp)
            result = _run_setup(empty_dir, extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertFalse(payload["agy"]["installed"])
        self.assertIsNone(payload["agy"]["version"])
        self.assertEqual(payload["auth"]["state"], "unknown")
        self.assertEqual(payload["agents"]["state"], "error")

    def test_human_report_is_plain_text_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(bin_dir, behavior="authenticated_with_agents")

        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(result.stdout)
        self.assertIn("code-auditor", result.stdout)
        self.assertIn("1.1.6", result.stdout)


class AgyVersionFloorTest(unittest.TestCase):
    """There is one supported agy version floor and no dual code path. The
    doctor must flag a below-floor version distinctly rather than silently
    printing the raw version string.

    These assert against `setup.MIN_AGY_VERSION_STR` rather than a literal,
    so raising the floor does not require editing them."""

    def test_below_floor_version_is_flagged_distinctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.1.6",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agy"]["version"], "1.1.6")
        self.assertEqual(payload["agy"]["min_version"], setup_module.MIN_AGY_VERSION_STR)
        self.assertFalse(payload["agy"]["version_supported"])

    def test_below_floor_version_reads_actionably_in_the_human_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir, behavior="authenticated_with_agents", agy_version="1.1.6"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        self.assertIn("1.1.6", result.stdout)
        self.assertIn("below minimum supported version", lowered)
        self.assertIn(setup_module.MIN_AGY_VERSION_STR, result.stdout)

    def test_at_floor_version_is_reported_as_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
            )

        payload = json.loads(result.stdout)
        self.assertTrue(payload["agy"]["version_supported"])

    def test_above_floor_version_is_reported_as_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.2.0",
            )

        payload = json.loads(result.stdout)
        self.assertTrue(payload["agy"]["version_supported"])

    def test_1_1_10_is_now_below_the_floor(self):
        """The floor moved from 1.1.10 to 1.1.11 (see MIN_AGY_VERSION's
        comment in setup.py): 1.1.10 fixed --model/--effort forwarding but
        could not PROVE it took effect; 1.1.11 can, via the free read-only
        slash-command probe. A binary that was previously AT the floor must
        now report below it — this is the regression guard for that bump."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.1.10",
            )

        payload = json.loads(result.stdout)
        self.assertFalse(payload["agy"]["version_supported"])


class StreamJsonCapabilityTest(unittest.TestCase):
    """The doctor's distinct, actionable signal for whether the installed
    agy supports `--output-format stream-json` — so an unsupported binary
    fails at the doctor rather than somewhere less legible."""

    def test_supporting_version_reports_stream_json_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["stream_json"]["state"], "supported")

    def test_non_supporting_version_reports_stream_json_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.1.6",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["stream_json"]["state"], "unsupported")

    def test_stream_json_state_is_plainly_reported_in_the_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir, behavior="authenticated_with_agents", agy_version="1.1.6"
            )

        lowered = result.stdout.lower()
        self.assertIn("stream-json", lowered)
        self.assertIn("not supported", lowered)

    def test_stream_json_is_skipped_cleanly_when_agy_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp)
            result = _run_setup(empty_dir, extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["stream_json"]["state"], "skipped")


class ReviewAgentBindTest(unittest.TestCase):
    """The setup doctor's bind check for the vendored `agy-review` agent
    (`plugins/agy/agents/agy-review/agent.md`): `agy agents` only ever lists
    GLOBAL custom agents, so it can never see this
    workspace-scoped agent. The doctor instead uses the free `--model
    <invalid>` bind probe from docs/review-schema-verdict.md Finding A,
    reading the fake agy's --log-file — never stdout or the exit code.

    The probe can only ever DISPROVE a **bind**, never prove
    one — the zero-quota `--model <invalid>` path exits before any
    conversation is created, so a `Created conversation` line (the only
    genuine positive **bind proof**) is structurally unreachable here. The
    real free-probe shape (agent resolution reached, no fallback line) used
    to be misread as "bound"; it is now correctly "unknown", and the human
    report must not claim the agent bound or that /agy:review "will work"."""

    def test_reports_unknown_when_the_probe_shows_no_fallback(self):
        """"bind_probe_no_fallback" (renamed from "bind_probe_bound") is the
        REAL free-probe shape: `printmode.go` agent-resolution lines, no
        fallback line, no `Created conversation` line. Before that was understood, this
        state was misreported as "bound" on the strength of the printmode.go
        lines alone — a claim the zero-quota probe cannot actually support."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                bind_probe_behavior="bind_probe_no_fallback",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["review_bind"]["state"], "unknown")
        self.assertNotEqual(payload["review_bind"]["state"], "bound")

    def test_human_report_does_not_claim_bound_or_will_work_when_no_fallback_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                bind_probe_behavior="bind_probe_no_fallback",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        self.assertIn("agy-review", lowered)
        self.assertNotIn("agy-review agent: bound", result.stdout)
        self.assertNotIn("will work", lowered)
        self.assertIn("agent fallback", lowered)
        self.assertIn("unknown", lowered)

    def test_reports_not_bound_with_actionable_guidance_when_the_agent_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                bind_probe_behavior="bind_probe_not_bound",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["review_bind"]["state"], "not_bound")

    def test_human_report_tells_the_user_what_to_do_when_the_agent_does_not_bind(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                bind_probe_behavior="bind_probe_not_bound",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        self.assertIn("not bound", lowered)
        self.assertIn("/agy:review will not work", lowered)
        # Actionable guidance: what the user should check.
        self.assertIn("agent.md", lowered)
        self.assertIn("version", lowered)

    def test_bind_probe_is_skipped_cleanly_when_agy_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp)
            result = _run_setup(empty_dir, extra_args=["--json"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["review_bind"]["state"], "skipped")

    def test_bind_probe_dispatch_is_independent_of_the_agents_listing_behavior(self):
        """The doctor makes two distinct agy calls per run (`agy agents`,
        then the bind probe) — this proves they are dispatched off separate
        env vars and don't leak into each other: an agents-listing failure
        must not prevent the bind probe from running and reporting its own,
        independent result."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="agents_list_fails",
                extra_args=["--json"],
                bind_probe_behavior="bind_probe_no_fallback",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["agents"]["state"], "error")
        self.assertEqual(payload["review_bind"]["state"], "unknown")

    def test_reports_unknown_when_the_probe_log_carries_no_signal_at_all(self):
        """The defect this mission fixes, live-observed on 2026-07-30:
        `_bind_check` used to infer "bound" from the mere absence of a
        fallback line, so an empty/no-signal log — zero evidence either
        way — silently passed as a confirmed bind. It must now report
        "unknown", distinct from both "bound" and "not_bound"."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                bind_probe_behavior="bind_probe_unknown",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["review_bind"]["state"], "unknown")
        self.assertNotEqual(payload["review_bind"]["state"], "bound")

    def test_human_report_surfaces_unknown_distinctly_from_bound_and_not_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                bind_probe_behavior="bind_probe_unknown",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        lowered = result.stdout.lower()
        self.assertIn("unknown", lowered)
        self.assertNotIn("agy-review agent: bound", result.stdout)
        self.assertNotIn("not bound", lowered)


class BindProbeCommandVectorTest(unittest.TestCase):
    """`_bind_probe_command` is pure — no I/O — so it is asserted directly,
    mirroring `review._agy_command`'s own pure-command test
    (tests/test_review.py's AgyCommandVectorRequestsEventStreamTest)."""

    def test_carries_disable_slash_commands(self):
        cmd = setup_module._bind_probe_command("agy", "/tmp/agy.log")
        self.assertIn("--disable-slash-commands", cmd)

    def test_vector_matches_the_contract_exactly(self):
        cmd = setup_module._bind_probe_command("agy", "/tmp/agy.log")
        self.assertEqual(cmd, [
            "agy", "-p", setup_module._BIND_PROBE_PROMPT,
            "--disable-slash-commands",
            "--agent", setup_module._REVIEW_AGENT_NAME,
            "--model", setup_module._BIND_PROBE_MODEL,
            "--sandbox",
            "--new-project",
            "--log-file", "/tmp/agy.log",
            "--print-timeout",
            launch._print_timeout_arg(setup_module._BIND_PROBE_TIMEOUT_SECONDS),
        ])

    def test_carries_print_timeout_for_fidelity_with_reviews_real_vector(self):
        """`_probe_review_bind`'s docstring states fidelity to the real
        `review._agy_command` vector is "the whole point" of this probe.
        `--print-timeout` can never functionally matter here (this probe
        always exits on local `--model` validation before any conversation
        is created — Finding A), but omitting a flag the real vector always
        carries now would be exactly the silent divergence that docstring
        warns against, for a flag that costs nothing to add."""
        cmd = setup_module._bind_probe_command("agy", "/tmp/agy.log")
        self.assertIn("--print-timeout", cmd)
        # Must carry a unit, never a bare integer (agy exit-2 crash) — same
        # discipline as launch._print_timeout_arg's own regression test.
        self.assertRegex(cmd[cmd.index("--print-timeout") + 1], r"^\d+s$")


class ModelForwardingProofTest(unittest.TestCase):
    """`setup._probe_model_forwarding` proves — never assumes — that
    `--model` forwarded to `agy -p` actually takes effect, by forwarding a
    model id that is DELIBERATELY DIFFERENT from the baseline (chosen from
    `agy models`) and checking the answer changed to match. All of these
    run at `MIN_AGY_VERSION_STR` or above so the probe is not itself
    skipped for being below floor."""

    def test_confirmed_when_agy_genuinely_applies_the_differing_forwarded_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        mf = payload["model_forwarding"]
        self.assertEqual(mf["state"], "confirmed")
        # The whole point of the fix: the forwarded model must differ from
        # the baseline (otherwise the probe proves nothing), and the
        # effective model must equal what was forwarded, not the baseline.
        self.assertNotEqual(mf["requested_model"], mf["baseline_model"])
        self.assertEqual(mf["requested_model"], mf["effective_model"])

    def test_human_report_shows_confirmed_forwarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        lowered = result.stdout.lower()
        self.assertIn("--model forwarding: confirmed", lowered)

    def test_mismatch_is_reported_as_a_loud_actionable_failure(self):
        """The exact silent-fallback bug class MIN_AGY_VERSION exists to
        guard against: agy answers a DIFFERENT model than what was
        forwarded. Must never be reported as "confirmed"."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_mismatch",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        mf = payload["model_forwarding"]
        self.assertEqual(mf["state"], "mismatch")
        self.assertNotEqual(mf["requested_model"], mf["effective_model"])

    def test_human_report_flags_a_mismatch_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_mismatch",
            )

        self.assertIn("MISMATCH", result.stdout)
        self.assertIn("silent-fallback", result.stdout.lower())

    def test_a_binary_that_ignores_model_and_always_answers_the_persisted_default_is_caught(self):
        """THE REGRESSION GUARD. The first version of
        `_probe_model_forwarding` forwarded the SAME value it had just read
        as the baseline, so a binary that silently ignores `--model` and
        always answers with its persisted default was indistinguishable
        from one that genuinely honors it — both produce the same answer
        when the forwarded value equals the baseline. Against
        "probe_ignores_model" (a fake that answers with one fixed model no
        matter what was forwarded, or not), the FIXED check — which
        forwards a value that deliberately differs from the baseline —
        must report "mismatch" (or "unknown"), and must NEVER report
        "confirmed". If this test is ever green with state == "confirmed",
        the tautology is back."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_ignores_model",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        state = payload["model_forwarding"]["state"]
        self.assertIn(state, ("mismatch", "unknown"))
        self.assertNotEqual(state, "confirmed")

    def test_unknown_when_the_model_probe_cannot_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_error",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "unknown")

    def test_unknown_when_agy_models_cannot_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
                models_behavior="models_fails",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "unknown")

    def test_unknown_when_agy_models_output_is_unparseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
                models_behavior="models_garbage",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "unknown")

    def test_unknown_when_agy_models_lists_fewer_than_two_distinct_models(self):
        """Nothing differs from the baseline to forward — cannot
        discriminate honoring from ignoring, so this must be "unknown",
        never "confirmed" by default."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
                models_behavior="models_too_few",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "unknown")

    def test_skipped_below_floor(self):
        """Below MIN_AGY_VERSION, /model is not guaranteed to answer for
        free at all — the doctor must not spend live quota running this
        probe on an unsupported binary."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.1.6",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "skipped")

    def test_skipped_cleanly_when_agy_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp)
            result = _run_setup(empty_dir, extra_args=["--json"])

        payload = json.loads(result.stdout)
        self.assertEqual(payload["model_forwarding"]["state"], "skipped")


class ModelsCommandVectorTest(unittest.TestCase):
    """`probe._models_command` and `probe._parse_models` are pure — no I/O
    — asserted directly here alongside their consumer's coverage (mirrors
    `tests/test_probe.py`, which also covers them from `companion.probe`'s
    own side)."""

    def test_models_command_vector(self):
        cmd = probe_module._models_command("agy")
        self.assertEqual(cmd, ["agy", "models"])

    def test_parse_models_skips_header_and_blank_lines(self):
        stdout = "Fetching available models...\n\ngemini-x\tGemini X\n"
        self.assertEqual(probe_module._parse_models(stdout), [("gemini-x", "Gemini X")])

    def test_parse_models_skips_lines_with_no_tab(self):
        stdout = "Fetching available models...\nno tab on this line\ngemini-x\tGemini X\n"
        self.assertEqual(probe_module._parse_models(stdout), [("gemini-x", "Gemini X")])


class QuotaReportTest(unittest.TestCase):
    """`setup._probe_quota` reads /usage (never /credits — see
    setup.py's comment on why) and surfaces per-group, per-bucket quota
    remaining."""

    def test_ok_state_reports_groups_and_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        quota = payload["quota"]
        self.assertEqual(quota["state"], "ok")
        self.assertEqual(len(quota["groups"]), 2)
        gemini_group = quota["groups"][0]
        self.assertEqual(gemini_group["name"], "Gemini Models")
        self.assertEqual(len(gemini_group["buckets"]), 2)

    def test_bucket_with_no_description_does_not_crash_and_reads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        payload = json.loads(result.stdout)
        buckets = payload["quota"]["groups"][0]["buckets"]
        weekly = next(b for b in buckets if b["id"] == "gemini-weekly")
        five_hour = next(b for b in buckets if b["id"] == "gemini-5h")
        self.assertIsNotNone(weekly["description"])
        self.assertIsNone(five_hour["description"])

    def test_human_report_surfaces_bucket_name_percentage_and_reset_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        self.assertIn("Weekly Limit Remaining", result.stdout)
        self.assertIn("99.7%", result.stdout)
        self.assertIn("2026-08-14T07:11:25Z", result.stdout)

    def test_unknown_when_the_usage_probe_cannot_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_error",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["quota"]["state"], "unknown")
        self.assertIsNone(payload["quota"]["groups"])

    def test_skipped_below_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version="1.1.6",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["quota"]["state"], "skipped")

    def test_never_reads_credits_instead_of_usage(self):
        """/credits is NOT the quota signal (see setup.py's comment): it
        reports a credits BALANCE, which reads 0 on a subscription account
        even when weekly/5h quota is nearly full. This test exists so
        nobody "fixes" _probe_quota to call /credits instead — the fake's
        "probe_default" /credits answer (remaining_credits: 0) must never
        leak into the quota report."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            _install_fake_agy(bin_dir)
            result = _run_setup(
                bin_dir,
                behavior="authenticated_with_agents",
                extra_args=["--json"],
                agy_version=setup_module.MIN_AGY_VERSION_STR,
                probe_behavior="probe_default",
            )

        payload = json.loads(result.stdout)
        self.assertNotIn("remaining_credits", json.dumps(payload["quota"]))


class NormalizeUsageGroupsTest(unittest.TestCase):
    """`setup._normalize_usage_groups` is a pure, tolerant parse of agy's
    own JSON shape — asserted directly so a malformed real-world answer is
    covered without needing a subprocess round-trip for every shape."""

    def test_missing_groups_key_is_none(self):
        self.assertIsNone(setup_module._normalize_usage_groups(None))

    def test_groups_not_a_list_is_none(self):
        self.assertIsNone(setup_module._normalize_usage_groups({"oops": "not a list"}))

    def test_empty_list_is_a_legitimate_empty_answer(self):
        self.assertEqual(setup_module._normalize_usage_groups([]), [])

    def test_non_dict_group_entries_are_skipped_not_crashed_on(self):
        groups = setup_module._normalize_usage_groups(["not a dict", 42, None])
        self.assertEqual(groups, [])

    def test_missing_buckets_key_yields_empty_bucket_list(self):
        groups = setup_module._normalize_usage_groups([{"name": "G"}])
        self.assertEqual(groups, [{"name": "G", "description": None, "buckets": []}])

    def test_buckets_not_a_list_yields_empty_bucket_list(self):
        groups = setup_module._normalize_usage_groups([{"name": "G", "buckets": "oops"}])
        self.assertEqual(groups, [{"name": "G", "description": None, "buckets": []}])

    def test_non_dict_bucket_entries_are_skipped(self):
        groups = setup_module._normalize_usage_groups(
            [{"name": "G", "buckets": ["not a dict", {"id": "b1"}]}]
        )
        self.assertEqual(len(groups[0]["buckets"]), 1)
        self.assertEqual(groups[0]["buckets"][0]["id"], "b1")

    def test_bucket_missing_description_reads_as_none(self):
        groups = setup_module._normalize_usage_groups(
            [{"name": "G", "buckets": [{"id": "b1", "remaining_fraction": 0.5}]}]
        )
        self.assertIsNone(groups[0]["buckets"][0]["description"])
        self.assertEqual(groups[0]["buckets"][0]["remaining_fraction"], 0.5)


class SetupSubprocessDevnullStdinTest(unittest.TestCase):
    def test_probe_version_passes_devnull_stdin(self):
        with mock.patch.object(
            setup_module.subprocess, "run",
            return_value=mock.MagicMock(returncode=0, stdout="1.1.19\n", stderr="")
        ) as mock_run:
            setup_module._probe_version("agy")

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_probe_agents_passes_devnull_stdin(self):
        with mock.patch.object(
            setup_module.subprocess, "run",
            return_value=mock.MagicMock(returncode=0, stdout="code-auditor\n", stderr="")
        ) as mock_run:
            setup_module._probe_agents("agy")

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_probe_review_bind_passes_devnull_stdin(self):
        with mock.patch.object(
            setup_module.subprocess, "run",
            return_value=mock.MagicMock(returncode=1, stdout="", stderr="invalid model")
        ) as mock_run:
            setup_module._probe_review_bind("agy")

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()

