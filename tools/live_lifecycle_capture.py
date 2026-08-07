#!/usr/bin/env python3
"""tools/live_lifecycle_capture.py — drive the whole background **job**
lifecycle through the shipped companion against a real `agy`, and assert what
actually happened.

The other two live tools probe agy's *log traces* in isolation. This one
exercises the commands a user actually runs, in sequence, and checks the
observable outcome of each:

  A. delegate --background -> status(running) -> status(completed) -> result
  B. review   --background -> status          -> result            (the
     review path stages an **agent workspace** that delegate does not, so a
     passing delegate job proves nothing about it)
  C. cancel: launch, confirm the PID is alive, cancel, confirm it is DEAD

NEVER invoked by `make check` or any validator. It spends real quota and
needs an authenticated agy — `make check-live` runs it deliberately.

Why it exists. Every one of these commands passed 175 offline tests against a
fake agy while `/agy:cancel` reported kills it never verified. That bug was
found by hand-driving this exact sequence once; nothing re-ran it afterwards.
Phase C is the reason this file is not just a smoke test: it asserts the
process is gone, not that the command said so.

Everything runs against a throwaway scratch repo with an isolated
CLAUDE_PLUGIN_DATA, so it never touches this repo or a developer's real job
state.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPANION = REPO_ROOT / "plugins" / "agy" / "scripts" / "agy_companion.py"

# Sentinels: the delegate task asks for one exact word back, so the harvested
# result can be checked for content rather than merely for being non-empty.
_DELEGATE_SENTINEL = "BRAVO"
_DELEGATE_TASK = "Reply with exactly the word: " + _DELEGATE_SENTINEL
# Long enough to still be streaming when phase C cancels it.
_LONG_TASK = (
    "Write a detailed 4000-word technical essay on the history of distributed "
    "consensus algorithms, covering Paxos, Raft, and Byzantine fault tolerance "
    "in depth."
)

_SEEDED_GOOD = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'
_SEEDED_BUGGY = (
    'def add(a, b):\n    """Add two numbers."""\n    return a - b  # bug: should be a + b\n'
)

_POLL_INTERVAL_SECONDS = 2


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), check=True, capture_output=True)


def bootstrap_scratch_repo(root):
    """A throwaway git repo with an uncommitted one-line bug, so `review` has
    a real working-tree diff to resolve.

    Note what is deliberately absent: no `.agents/` staging. The companion
    stages its own agent workspace now (companion.agent_workspace); a probe
    that hand-placed the agent would re-mask exactly the bug that shipped."""
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "scratch@example.invalid"], root)
    _git(["config", "user.name", "scratch"], root)
    calc = root / "calc.py"
    calc.write_text(_SEEDED_GOOD, encoding="utf-8")
    _git(["add", "."], root)
    _git(["commit", "-q", "-m", "init"], root)
    calc.write_text(_SEEDED_BUGGY, encoding="utf-8")


def _companion(args, cwd, env, timeout):
    return subprocess.run(
        [sys.executable, str(COMPANION)] + args,
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=timeout,
    )


def _status_rows(cwd, env, timeout):
    result = _companion(["status", "--json", "--all-sessions"], cwd, env, timeout)
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return []


def _row_for(rows, job_id):
    for row in rows:
        if row.get("id") == job_id:
            return row
    return None


def _launched_job_id(stdout):
    """The launch line reads `Launched background <kind> job <id> (log: ...)`."""
    for token in stdout.split():
        if token.startswith(("delegate-", "review-")):
            return token
    return None


def _await_status(cwd, env, job_id, wanted, budget, timeout):
    """Poll /agy:status until `job_id` reports one of `wanted`. Returns the
    final status (or None if the job vanished)."""
    deadline = time.monotonic() + budget
    last = None
    while True:
        row = _row_for(_status_rows(cwd, env, timeout), job_id)
        last = row.get("status") if row else None
        if last in wanted or time.monotonic() >= deadline:
            return last
        time.sleep(_POLL_INTERVAL_SECONDS)


def _job_pid(plugin_data, job_id):
    for state_file in pathlib.Path(plugin_data).glob("state/*/state.json"):
        try:
            jobs = json.loads(state_file.read_text(encoding="utf-8")).get("jobs", [])
        except (ValueError, OSError):
            continue
        for job in jobs:
            if job.get("id") == job_id:
                return job.get("pid")
    return None


def _pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _fail(phase, detail):
    print("FAIL[{}]: {}".format(phase, detail), file=sys.stderr)
    return False


def phase_delegate(cwd, env, budget, timeout):
    """A. The delegate background lifecycle, end to end."""
    launch = _companion(["delegate", "--background", _DELEGATE_TASK], cwd, env, timeout)
    job_id = _launched_job_id(launch.stdout)
    if not job_id:
        return _fail("delegate", "no job id in launch output: {!r}".format(launch.stdout[:200]))
    print("delegate_job:", job_id)

    # Not asserted as "must be running": a fast agy can legitimately finish
    # before the first poll. Asserted only that status KNOWS the job.
    first = _row_for(_status_rows(cwd, env, timeout), job_id)
    if first is None:
        return _fail("delegate", "status does not list the job it just launched")
    print("delegate_first_status:", first.get("status"))

    final = _await_status(cwd, env, job_id, {"completed"}, budget, timeout)
    print("delegate_final_status:", final)
    if final != "completed":
        return _fail(
            "delegate",
            "job never reached 'completed' within {}s (last: {}). The completion "
            "marker may have drifted — /agy:status would then report every "
            "background job as running forever.".format(budget, final),
        )

    harvested = _companion(["result", job_id], cwd, env, timeout)
    print("delegate_result:", harvested.stdout.strip()[:80])
    if _DELEGATE_SENTINEL not in harvested.stdout:
        return _fail(
            "delegate",
            "result did not contain the sentinel {!r} — stdout capture is "
            "broken or the wrong job was harvested".format(_DELEGATE_SENTINEL),
        )
    return True


def phase_review(cwd, env, budget, timeout):
    """B. The background REVIEW lifecycle. Distinct from delegate because
    review binds a vendored agent via a staged agent workspace; delegate
    never passes --agent at all, so it exercises none of that."""
    launch = _companion(["review", "--background"], cwd, env, timeout)
    job_id = _launched_job_id(launch.stdout)
    if not job_id:
        return _fail("review", "no job id in launch output: {!r}".format(launch.stdout[:200]))
    print("review_job:", job_id)

    final = _await_status(cwd, env, job_id, {"completed"}, budget, timeout)
    print("review_final_status:", final)
    if final != "completed":
        return _fail("review", "job never reached 'completed' within {}s (last: {})".format(
            budget, final))

    harvested = _companion(["result", job_id], cwd, env, timeout)
    text = harvested.stdout
    print("review_result_bytes:", len(text))
    # A silent fallback produces prose from agy's default agent, not the
    # rendered finding table — this is the assertion that the agent workspace
    # actually bound in a DETACHED run, which no offline test can prove.
    if "Verdict:" not in text and "Priority" not in text:
        return _fail(
            "review",
            "harvested result is not a rendered review (no verdict/priority "
            "table). The background review likely fell back to agy's default "
            "agent — i.e. the agent workspace did not reach the detached "
            "process. Raw head: {!r}".format(text.strip()[:200]),
        )
    return True


def phase_cancel(cwd, env, plugin_data, timeout):
    """C. The one that matters: cancel must leave the process DEAD, not merely
    say it did."""
    launch = _companion(["delegate", "--background", _LONG_TASK], cwd, env, timeout)
    job_id = _launched_job_id(launch.stdout)
    if not job_id:
        return _fail("cancel", "no job id in launch output")
    print("cancel_job:", job_id)

    pid = _job_pid(plugin_data, job_id)
    if not _pid_alive(pid):
        return _fail(
            "cancel",
            "job pid {} was not alive before cancelling — the task finished too "
            "fast to be a meaningful cancel test".format(pid),
        )
    print("cancel_pid_alive_before:", pid)

    cancelled = _companion(["cancel", job_id], cwd, env, timeout)
    print("cancel_output:", cancelled.stdout.strip()[:120])
    if cancelled.returncode != 0:
        return _fail("cancel", "cancel exited {}: {}".format(
            cancelled.returncode, cancelled.stderr.strip()[:200]))

    # The assertion the old code could not have passed: the command has
    # returned, so the process must already be gone. No grace period here on
    # purpose — cancel is responsible for having waited.
    if _pid_alive(pid):
        return _fail(
            "cancel",
            "cancel returned success but pid {} is STILL ALIVE. It is still "
            "spending quota, and because 'cancelled' short-circuits status "
            "rendering it will never appear in /agy:status again.".format(pid),
        )
    print("cancel_pid_alive_after:", False)

    row = _row_for(_status_rows(cwd, env, timeout), job_id)
    status = row.get("status") if row else None
    print("cancel_final_status:", status)
    if status != "cancelled":
        return _fail("cancel", "expected status 'cancelled', got {!r}".format(status))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", default=None, help="throwaway dir (created fresh)")
    parser.add_argument(
        "--budget",
        type=int,
        default=180,
        help="seconds to wait for a background job to reach a terminal status",
    )
    parser.add_argument("--timeout", type=int, default=120, help="per-command timeout, seconds")
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=["delegate", "review", "cancel"],
        help="skip a phase (repeatable) — e.g. --skip review to spend less quota",
    )
    args = parser.parse_args()

    holder = args.scratch_dir or tempfile.mkdtemp(prefix="agy-lifecycle-")
    root = pathlib.Path(holder)
    repo = root / "repo"
    plugin_data = root / "plugindata"
    plugin_data.mkdir(parents=True, exist_ok=True)
    bootstrap_scratch_repo(repo)

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    env["AGY_COMPANION_SESSION_ID"] = "live-lifecycle"

    print("scratch_dir:", root)
    phases = [
        ("delegate", lambda: phase_delegate(repo, env, args.budget, args.timeout)),
        ("review", lambda: phase_review(repo, env, args.budget, args.timeout)),
        ("cancel", lambda: phase_cancel(repo, env, plugin_data, args.timeout)),
    ]

    results = {}
    for name, run_phase in phases:
        if name in args.skip:
            print("--- phase {}: SKIPPED".format(name))
            continue
        print("--- phase {} ---".format(name))
        try:
            results[name] = run_phase()
        except subprocess.TimeoutExpired:
            results[name] = _fail(name, "a companion command timed out")
        except FileNotFoundError:
            print("agy is not installed or not on PATH", file=sys.stderr)
            return 1

    for name, ok in results.items():
        print("phase_{}:".format(name), "ok" if ok else "FAILED")
    all_ok = all(results.values()) if results else False
    print("result:", "ok" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
