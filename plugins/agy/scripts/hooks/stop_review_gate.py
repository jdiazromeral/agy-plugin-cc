#!/usr/bin/env python3
"""Stop hook: optional stop-time review gate for agy companion.

Runs a fast, hermetic agy check on Claude Code's turn modifications before
Claude Code stops or yields control.

Safety invariants (non-negotiable):
1. Strictly opt-in: default disabled; if disabled, exits 0 with ALLOW in <2ms.
2. Continuation check: if input indicates continuation_active, exits 0 immediately.
3. Circuit breaker: max 1 block per session turn sequence to avoid infinite loops.
4. Dirty check: exits 0 immediately if working tree has no uncommitted changes.
5. Fast parameterization: uses --model gemini-3.8-flash-high --effort low --print-timeout 15s with 20s subprocess timeout.
6. Fail-open: on timeout, crash, or unexpected output, emits ALLOW with a warning to stderr.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# Insert companion scripts directory to sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from companion import state  # noqa: E402
from companion.git import ensure_git_repository, GitTargetError  # noqa: E402

_PROMPT_TEMPLATE_PATH = _SCRIPTS_DIR.parent / "prompts" / "stop-review-gate.md"


def _emit_decision(decision, reason=None):
    # Claude Code's Stop-hook schema only accepts "approve" | "block" in the
    # top-level `decision` field. Internally we say "allow"; translate here.
    if decision == "allow":
        decision = "approve"
    payload = {"decision": decision}
    if reason:
        payload["reason"] = reason
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _is_git_dirty(repo_root):
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=5,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return False


def _check_circuit_breaker(session_id, repo_root):
    """Returns True if already blocked once in this session turn sequence."""
    if not session_id:
        return False
    state_dir = state.resolve_state_dir(repo_root)
    marker_file = state_dir / "stop_gate_{}.marker".format(session_id)
    if marker_file.exists():
        try:
            marker_file.unlink()
        except OSError:
            pass
        return True
    return False


def _trip_circuit_breaker(session_id, repo_root):
    if not session_id:
        return
    state_dir = state.resolve_state_dir(repo_root)
    state.ensure_state_dir(repo_root)
    marker_file = state_dir / "stop_gate_{}.marker".format(session_id)
    try:
        marker_file.write_text("blocked\n", encoding="utf-8")
    except OSError:
        pass


def _load_prompt_template():
    if _PROMPT_TEMPLATE_PATH.is_file():
        return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return "<task>\nRun a stop-gate review of the previous Claude turn.\n{{CLAUDE_RESPONSE_BLOCK}}\n</task>"


def run_stop_review(repo_root, input_payload):
    last_assistant_msg = str(input_payload.get("last_assistant_message") or "").strip()
    claude_block = (
        "Previous Claude response:\n{}".format(last_assistant_msg)
        if last_assistant_msg
        else ""
    )
    template = _load_prompt_template()
    prompt = template.replace("{{CLAUDE_RESPONSE_BLOCK}}", claude_block)

    cmd = [
        "agy",
        "-p",
        "--model", "gemini-3.8-flash-high",
        "--effort", "low",
        "--print-timeout", "15s",
        "--dangerously-skip-permissions",
        "--sandbox",
        "--disable-slash-commands",
        prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("agy stop-time review timed out after 20s; failing open (ALLOW).\n")
        return True, None
    except Exception as exc:
        sys.stderr.write("agy stop-time review execution error: {}; failing open (ALLOW).\n".format(exc))
        return True, None

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        sys.stderr.write("agy stop-time review non-zero exit ({}): {}; failing open (ALLOW).\n".format(proc.returncode, detail))
        return True, None

    output = proc.stdout.strip()
    first_line = output.splitlines()[0].strip() if output else ""

    if first_line.startswith("ALLOW:"):
        return True, None
    if first_line.startswith("BLOCK:"):
        reason = first_line[len("BLOCK:"):].strip() or output
        return False, "agy stop-time review found issues that need fixes: " + reason

    sys.stderr.write("agy stop-time review unexpected output format: {!r}; failing open (ALLOW).\n".format(first_line))
    return True, None


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        repo_root = ensure_git_repository(cwd)
    except (GitTargetError, Exception):
        _emit_decision("allow")
        return 0

    # 1. Opt-in check (default: disabled)
    config = state.get_config(repo_root)
    if not config.get("stop_review_gate", False):
        _emit_decision("allow")
        return 0

    # 2. Continuation active check
    if payload.get("continuation_active"):
        _emit_decision("allow")
        return 0

    session_id = payload.get("session_id") or os.environ.get(state.SESSION_ID_ENV)

    # 3. Circuit breaker check (maximum 1 block per turn sequence)
    if _check_circuit_breaker(session_id, repo_root):
        sys.stderr.write("agy stop-time review: circuit breaker ceiling reached; allowing stop.\n")
        _emit_decision("allow")
        return 0

    # 4. Dirty check
    if not _is_git_dirty(repo_root):
        _emit_decision("allow")
        return 0

    # 5. Run review check
    allow, block_reason = run_stop_review(repo_root, payload)
    if not allow:
        _trip_circuit_breaker(session_id, repo_root)
        _emit_decision("block", block_reason)
        return 0

    _emit_decision("allow")
    return 0


if __name__ == "__main__":
    sys.exit(main())
