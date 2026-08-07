#!/usr/bin/env python3
"""tools/live_delegate_capture.py — re-verify, against whatever `agy` is
currently on PATH, that the log traces the delegate/status/result path
depends on still say what the shipped regexes expect.

Companion to `tools/live_review_capture.py`, same rules: NEVER invoked by
`make check` or any validator. Two uses:

  1. Manual capture — refresh `tests/fixtures/delegate/*.log` after an agy
     upgrade, then update `PROVENANCE.md`.
  2. `make check-live` — re-run the probes and exit non-zero if any expected
     trace has drifted.

Why this exists: the offline suite drives `tests/fake_agy.py`, whose log
lines were originally written from memory. That let two real defects ship
green (see tests/test_log_fidelity.py). A fake is only as good as the last
time someone checked it against the binary; this is that check, automated.

Cost. Probe 1 (the silent-fallback trace, the plugin's most dangerous guard)
is **free** — conversation resolution is logged before the local `--model`
validation exits, so passing a deliberately invalid model captures the full
trace with no conversation created and no model call, exactly as Finding A
established for the agent bind check. Probe 2 spends ONE trivial live run,
because a completion marker cannot exist without a stream that completed.
Pass --free-only to run probe 1 alone and spend nothing.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "agy" / "scripts"))

from companion.agy_log import COMPLETION_MARKER_RE, CREATED_CONVERSATION_RE  # noqa: E402
from companion.delegate import CONVERSATION_NOT_FOUND_RE  # noqa: E402

# Any syntactically valid uuid that cannot exist. Kept in sync with
# tests/fixtures/delegate/2026-07-30-resume-fallback.log.
_BOGUS_UUID = "00000000-0000-4000-8000-000000000000"
_BOGUS_MODEL = "bogus-model-xyz"
# Deliberately trivial: this probe buys one log trace, not a work product.
_TRIVIAL_TASK = "Reply with exactly the word: OK"

# Traces that must NOT appear in a run advertised as zero-quota.
_QUOTA_MARKERS = ("Created conversation", "streamGenerateContent")


# agy writes the authenticated account into every run's log:
#   server_oauth.go:193] OAuth: authenticated successfully as <email>
# Any log captured as a fixture therefore carries the capturer's identity
# into a public repo unless it is removed. Doing that by inspection failed
# once already — the OAuth lines sort below ~20 lines of startup noise, so a
# truncated grep reads as clean. Scrub on the way IN, not by review.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_PLACEHOLDER = "user@example.invalid"

# Paths leak the capturer's home directory and workspace layout.
_HOME_RE = re.compile(r"/Users/[^/\s]+")
_HOME_PLACEHOLDER = "/Users/REDACTED"


def scrub(text):
    """Remove capturer identity from a raw agy log before it is written
    anywhere durable. Deliberately blunt: over-redaction costs nothing here,
    since no test asserts on an email or a home path."""
    return _HOME_RE.sub(_HOME_PLACEHOLDER, _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, text))


def write_fixture(path, text):
    """The only supported way to persist a captured log."""
    path.write_text(scrub(text), encoding="utf-8")
    return path


def _run(cmd, cwd, timeout):
    return subprocess.run(
        cmd, cwd=str(cwd), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=timeout,
    )


def probe_resume_fallback(scratch, timeout):
    """FREE. Ask agy to resume a conversation that cannot exist, with an
    invalid model so it exits before any model call. Verifies the fallback
    trace still matches CONVERSATION_NOT_FOUND_RE."""
    log_file = scratch / "resume-fallback.log"
    _run([
        "agy", "-p", "ping",
        "--disable-slash-commands",
        "--dangerously-skip-permissions", "--sandbox",
        "--conversation", _BOGUS_UUID,
        "--model", _BOGUS_MODEL,
        "--log-file", str(log_file),
    ], scratch, timeout)

    if not log_file.exists():
        print("FAIL[resume-fallback]: agy wrote no log file", file=sys.stderr)
        return False
    log = log_file.read_text(encoding="utf-8", errors="ignore")

    spent = [m for m in _QUOTA_MARKERS if m in log]
    if spent:
        print(
            "WARN[resume-fallback]: probe was expected to be free but the log "
            "contains {} — the zero-quota assumption has drifted.".format(spent),
            file=sys.stderr,
        )

    match = CONVERSATION_NOT_FOUND_RE.search(log)
    if not match:
        print(
            "FAIL[resume-fallback]: no line matched CONVERSATION_NOT_FOUND_RE. "
            "The silent-fallback guard is BLIND on this agy — /agy:delegate "
            "--resume can now report a fresh conversation as a resume.",
            file=sys.stderr,
        )
        return False

    # Both real lines must match on their own. The klog trace leaves the uuid
    # bare; the human-facing warning quotes it. Matching only the latter is
    # the exact regression this tool exists to catch.
    lines = [ln for ln in log.splitlines() if CONVERSATION_NOT_FOUND_RE.search(ln)]
    klog = [ln for ln in lines if "ignoring --conversation flag" in ln]
    print("resume_fallback_uuid:", match.group(1))
    print("resume_fallback_matching_lines:", len(lines))
    if not klog:
        print(
            "FAIL[resume-fallback]: the durable klog trace ('ignoring "
            "--conversation flag') did not match — the guard is resting on "
            "human-facing warning text alone.",
            file=sys.stderr,
        )
        return False
    return True


def probe_fresh_run(scratch, timeout):
    """SPENDS ONE LIVE RUN. A completion marker cannot be produced without a
    stream that actually completed, so this is irreducibly live."""
    log_file = scratch / "fresh.log"
    result = _run([
        "agy", "-p", _TRIVIAL_TASK,
        "--disable-slash-commands",
        "--dangerously-skip-permissions", "--sandbox", "--new-project",
        "--log-file", str(log_file),
    ], scratch, timeout)

    if not log_file.exists():
        print("FAIL[fresh]: agy wrote no log file", file=sys.stderr)
        return False
    log = log_file.read_text(encoding="utf-8", errors="ignore")

    print("fresh_exit_code:", result.returncode)
    created = CREATED_CONVERSATION_RE.search(log)
    completed = COMPLETION_MARKER_RE.search(log)
    print("created_conversation:", created.group(1) if created else None)
    print("completion_marker:", completed.group(1) if completed else None)

    ok = True
    if not created:
        print(
            "FAIL[fresh]: CREATED_CONVERSATION_RE did not match — /agy:delegate "
            "can no longer record a conversation, so --resume is broken.",
            file=sys.stderr,
        )
        ok = False
    if not completed:
        print(
            "FAIL[fresh]: COMPLETION_MARKER_RE did not match a run that "
            "completed — /agy:status will report every background job as "
            "'running' forever and /agy:result will never harvest one.",
            file=sys.stderr,
        )
        ok = False
    if created and completed and created.group(1) != completed.group(1):
        print("FAIL[fresh]: created and completed uuids differ", file=sys.stderr)
        ok = False
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", default=None, help="throwaway dir (created fresh)")
    parser.add_argument(
        "--free-only",
        action="store_true",
        help="run only the zero-quota resume-fallback probe; spend no live runs.",
    )
    parser.add_argument("--timeout", type=int, default=150, help="subprocess timeout, seconds")
    args = parser.parse_args()

    holder = None
    if args.scratch_dir is None:
        holder = tempfile.mkdtemp(prefix="agy-delegate-check-live-")
        scratch = pathlib.Path(holder)
    else:
        scratch = pathlib.Path(args.scratch_dir)
        scratch.mkdir(parents=True, exist_ok=True)

    print("scratch_dir:", scratch)
    try:
        ok = probe_resume_fallback(scratch, args.timeout)
        if not args.free_only:
            ok = probe_fresh_run(scratch, args.timeout) and ok
        else:
            print("skipped_fresh_run: --free-only (no live quota spent)")
    except subprocess.TimeoutExpired:
        print("TIMEOUT waiting for agy", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("agy is not installed or not on PATH", file=sys.stderr)
        return 1

    print("result:", "ok" if ok else "DRIFTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
