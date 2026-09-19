"""companion.launch — the shared **background launch** primitive, the
`--timeout` argument contract, and the `--print-timeout` derivation that
keeps agy's own timer racing INSIDE the plugin's, all in one home.

A **leaf module** (see glossary): it imports no subcommand module, so any of
`review.py`, `delegate.py`, and `adversarial_review.py` can import from it
without risking a circular import. It holds the one shared implementation of
`_spawn_detached`, `_AGY_TIMEOUT_SECONDS`, the `--timeout` `type=`
validator, `_print_timeout_arg`, and `_BACKGROUND_PRINT_TIMEOUT_ARG` — they
belong here rather than in any subcommand module precisely so no sibling has
to reach into another to get them.

--------------------------------------------------------------------------
WHY THIS MODULE ALSO OWNS --print-timeout
--------------------------------------------------------------------------
This plugin runs `agy -p` under its OWN subprocess timeout (`timeout=` on
`subprocess.run`, sourced from `_AGY_TIMEOUT_SECONDS` or `--timeout`). `agy`
ALSO has its own print-mode wait timeout, `--print-timeout`, whose default
is `5m0s` — exactly 300 seconds, i.e. exactly `_AGY_TIMEOUT_SECONDS`. Left
unset, the two timers race for the same deadline and whichever fires first
is undefined:

  - If agy's own timer wins, agy exits cleanly and emits a structured
    **result event** with `status: "ERROR"` and `error: "timeout waiting
    for response"` (see `companion.stream_events`'s `EventStream` docstring,
    and the real captured fixture at
    `tests/fixtures/error_result/2026-08-02-run1.ndjson`). That is the GOOD
    outcome: a legible, attributable failure `companion.result` already
    knows how to render.
  - If the plugin's subprocess timeout wins instead, the process is killed
    outright and there is no structured event to read.

Every **foreground launch** therefore passes an explicit `--print-timeout`
computed by `_print_timeout_arg` below, set to expire strictly BEFORE the
subprocess timeout — agy always loses the race, so the failure is always
the structured one.

**Verified Go-duration facts (agy 1.1.11, 2026-08-07 — see AGENTS.md
"Settled findings")**: `--print-timeout` parses as a Go duration and
REQUIRES a unit (`"290s"` works, bare `"290"` is a hard crash — exit 2,
`invalid value "290" for flag -print-timeout: time: missing unit in
duration "290"`). `_print_timeout_arg` therefore NEVER returns a bare
integer string — every value is suffixed with `"s"`.

--------------------------------------------------------------------------
WHY A **background launch** GETS A DIFFERENT, LARGE VALUE
--------------------------------------------------------------------------
A **background launch** is documented throughout this plugin as having NO
timeout ceiling by design (see the `--timeout cannot be combined with
--background` validation in `delegate.py`, `review.py`, and
`adversarial_review.py`). But agy's own `--print-timeout` defaults to
`5m0s` regardless of what the plugin does — so leaving it unset on a
background launch would silently reimpose a 5-minute ceiling on a job the
plugin explicitly promises has none. `_BACKGROUND_PRINT_TIMEOUT_ARG` closes
that gap: an explicit, generous, VERIFIED-to-parse value (`"24h"`), not
agy's silent 5-minute default. This is not a claim that agy has an
"unbounded" concept to opt into — it does not — so a large explicit bound is
the honest stand-in for "no ceiling", not a pretense of infinity.
"""
import argparse
import json
import subprocess

# Default foreground subprocess timeout, overridable per-call via
# --timeout (see positive_int_timeout below and each subcommand's
# add_arguments). Shared by review.py, delegate.py, and (through review's
# launch path) adversarial_review.py.
_AGY_TIMEOUT_SECONDS = 300

# How much earlier agy's own --print-timeout must expire than the plugin's
# subprocess timeout, so agy always loses the race (module docstring). 10s
# is generous headroom relative to the measured ~0.16s SIGTERM-to-exit
# latency (AGENTS.md "Settled findings") — ample room for agy to emit and
# flush its structured ERROR result event before the subprocess timeout
# would otherwise kill it outright.
_PRINT_TIMEOUT_MARGIN_SECONDS = 10

# Floor `_print_timeout_arg` never drops below, however small a caller's
# subprocess timeout is (a user CAN pass `--timeout 5`). Naive subtraction
# (5 - 10 = -5) would emit a negative duration, which is not a value agy is
# known to accept safely; clamping to this floor instead guarantees a
# strictly positive, valid duration is always emitted. 1s is the smallest a
# caller could plausibly want — not a tuned value, just "never zero or
# negative".
_MIN_PRINT_TIMEOUT_SECONDS = 1

# The explicit --print-timeout every BACKGROUND launch carries (module
# docstring: "WHY A background launch GETS A DIFFERENT, LARGE VALUE").
# Verified to parse on agy 1.1.11 (AGENTS.md "Settled findings": `agy -p
# "/model" --print-timeout 24h` exits 0). NOT arbitrary — it exists because
# agy's own 5m0s default would otherwise silently impose a 5-minute ceiling
# on a launch class this plugin explicitly promises has none.
_BACKGROUND_PRINT_TIMEOUT_ARG = "24h"


def positive_int_timeout(value):
    """argparse `type=` for `--timeout`: a positive whole number of
    seconds, or a clear rejection — never a silent coercion (a float string
    truncated, or a negative/zero value clamped up). Shared by every
    subcommand that accepts `--timeout` (review, delegate,
    adversarial-review) — the one public cross-module implementation, not
    another private copy."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "--timeout must be a positive whole number of seconds, got {!r}.".format(value)
        )
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "--timeout must be a positive whole number of seconds, got {!r}.".format(value)
        )
    return parsed


def _print_timeout_arg(subprocess_timeout_seconds):
    """Map a **foreground launch**'s subprocess timeout (whole seconds) to
    the `--print-timeout` argument STRING agy must always lose the race
    against (module docstring). Pure — no I/O — so it is trivially
    testable in isolation, the same discipline `_agy_command` builders
    across this plugin already follow for their own pure vectors.

    `subprocess_timeout_seconds - _PRINT_TIMEOUT_MARGIN_SECONDS`, floored at
    `_MIN_PRINT_TIMEOUT_SECONDS` so a small `--timeout` (a user CAN pass
    `--timeout 5`) can never drive the result negative or to zero — both of
    which would be nonsense durations to hand agy. The floor is deliberately
    coarse (never below 1s) rather than trying to preserve a "still less
    than the subprocess timeout" invariant down to the second at the small
    end: below the margin, losing the race cleanly is no longer possible
    anyway, and 1s is the smallest duration worth asking agy to honor at
    all.

    ALWAYS returns a string with an explicit unit suffix (`"s"`) — NEVER a
    bare integer. This is not cosmetic: agy's `--print-timeout` parses as a
    Go duration and a bare integer is a hard crash (verified, agy 1.1.11:
    `--print-timeout 290` exits 2, `missing unit in duration "290"` — see
    AGENTS.md "Settled findings"). `int` input only (matches
    `positive_int_timeout`'s contract and `_AGY_TIMEOUT_SECONDS`'s type) —
    this function does not itself validate that; callers already only ever
    pass a value `positive_int_timeout` or `_AGY_TIMEOUT_SECONDS` produced."""
    derived = subprocess_timeout_seconds - _PRINT_TIMEOUT_MARGIN_SECONDS
    seconds = max(_MIN_PRINT_TIMEOUT_SECONDS, derived)
    return "{}s".format(seconds)


def _spawn_detached(cmd, cwd, stdout_path=None):
    """The **background launch** seam: one small function that starts `cmd`
    fully detached — a new session so it survives the parent exiting, stdio
    redirected away from any pipe the parent could block reading — and
    returns immediately without waiting on it. Injectable: tests substitute
    this function itself (via `_run_background_review`'s `spawn` parameter)
    to exercise state-dir read/write and job-record creation without ever
    spawning a real process; the one test that does spawn something uses
    the extended fake `agy`, which writes its log and exits immediately.

    `stdout_path`, when given, redirects the child's stdout into that file
    instead of DEVNULL — the detached agy's stdout is the stored **result**,
    otherwise lost for good since there is no babysitter to hold onto it."""
    stdout_target = subprocess.DEVNULL
    stdout_handle = None
    if stdout_path is not None:
        stdout_handle = open(stdout_path, "wb")
        stdout_target = stdout_handle
    try:
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_target,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        # Popen dup()s the fd into the child on POSIX; our copy can close
        # immediately without truncating the child's writes.
        if stdout_handle is not None:
            stdout_handle.close()


def parse_agy_error(stderr_text):
    """Extract structured AGY_ERROR payload (agy 1.2.6+) from stderr if present.
    In agy 1.2.6+, headless API or agent failures emit a structured
    `AGY_ERROR: {"code": ..., "status": ..., "message": ...}` JSON line on stderr
    and exit with code 3."""
    if not stderr_text:
        return None
    for line in stderr_text.splitlines():
        line = line.strip()
        if line.startswith("AGY_ERROR:"):
            payload = line[len("AGY_ERROR:"):].strip()
            try:
                data = json.loads(payload)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None


def format_agy_error(stderr_text):
    """Format an agy stderr string for human presentation. If an AGY_ERROR
    payload is present, format it cleanly; otherwise return stripped stderr."""
    err = parse_agy_error(stderr_text)
    if err:
        msg = err.get("message") or err.get("error") or err.get("status")
        code = err.get("code") or err.get("error_code")
        if msg and code:
            return "agy error [{}]: {}".format(code, msg)
        if msg:
            return "agy error: {}".format(msg)
    return stderr_text.strip() if stderr_text else ""
