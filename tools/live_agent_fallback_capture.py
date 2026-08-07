#!/usr/bin/env python3
"""tools/live_agent_fallback_capture.py — spends real `agy -p` quota to
capture one **event stream** : a run
that requests a deliberately nonexistent `--agent` value and hits **agent
fallback** (the **silent fallback** variant for `--agent`, distinct from the
`--conversation`-driven **resume fallback**). No stdout or stderr signal
marks the fallback on the **event stream** itself — the captured run shows
the **init event**'s `init.agent` field echoing back the exact bogus name
that was requested, not the resolved default it actually fell back to. So
`init.agent` alone does NOT serve as a **structural bind proof** of which
agent bound: comparing the requested `--agent` value against `init.agent`
would misread this exact fallback run as a successful bind. That negative
finding — pinned by this capture — is why a **structural bind proof**
needs a different signal.

Sibling to `tools/live_stream_events_capture.py`, same discipline, but does
NOT import `live_review_capture.bootstrap_scratch_repo()`: that helper wires
up a workspace-scoped `agy-review` agent and a seeded diff so a *specific*
custom agent can bind. This capture wants the opposite — a bogus agent name
that can never bind to anything — so a plain scratch git repo (`git init` +
identity config, no agent, no diff) is the whole fixture and is simpler than
importing and ignoring half of the review bootstrap.

NEVER invoked by `make check` or any test in `tests/`. Manual capture only:
run once, persist the raw NDJSON stdout bytes to disk *before* any parsing,
then hand-copy the scrubbed bytes into `tests/fixtures/agent_fallback/` and
record a PROVENANCE.md entry. `tests/test_log_fidelity.py` parses those
committed bytes, never this tool and never a live `agy` run.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import collections
import json
import pathlib
import subprocess
import sys
import tempfile

# Obviously bogus, never a real agent name shipped by agy or this
# plugin — the whole point is that it cannot resolve.
BOGUS_AGENT = "definitely-nonexistent-agent-xyz"

PROMPT = "Say hello in five words."


def bootstrap_scratch_repo(root):
    """Create a throwaway, empty git repo at `root`. No agent, no seeded
    diff: the bogus --agent value doesn't need anything to bind to."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "config", "user.email", "scratch@example.invalid"], cwd=str(root), check=True
    )
    subprocess.run(["git", "config", "user.name", "scratch"], cwd=str(root), check=True)


def run_capture(scratch_root, log_file, timeout):
    # --agent BOGUS_AGENT deliberately does not exist, to trigger
    # **agent fallback**; --sandbox / --new-project / --output-format
    # stream-json / --log-file mirror live_stream_events_capture.py's flag
    # shape exactly. --disable-slash-commands is unconditional on every
    # command vector this plugin builds (glossary: **slash-command
    # expansion**), so this tool must carry it too: a capture taken with a
    # different argv than the companion really builds proves nothing about
    # the shipped command. See tests/fixtures/agent_fallback/PROVENANCE.md.
    cmd = [
        "agy", "-p", PROMPT,
        "--disable-slash-commands",
        "--agent", BOGUS_AGENT,
        "--sandbox",
        "--new-project",
        "--output-format", "stream-json",
        "--log-file", str(log_file),
    ]
    # Text=False (raw bytes) so the captured fixture can be written
    # to disk byte-for-byte, per the contract's "raw NDJSON bytes, byte-exact"
    # requirement.
    return subprocess.run(
        cmd,
        cwd=str(scratch_root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=False,
        timeout=timeout,
    )


def summarize_events(stdout_bytes):
    """Returns a Counter of event-type -> count, tolerant of any line that
    fails to parse (counted under "unparsable"). Summary only, printed for
    the operator to sanity-check before committing a fixture; the committed
    fixture bytes are always the raw stdout, never this summary."""
    counts = collections.Counter()
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            counts["unparsable"] += 1
            continue
        counts[obj.get("event", "(no event field)")] += 1
    return counts


def find_init_agent(stdout_bytes):
    """Returns the first init event's init.agent field, or None."""
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "init":
            return obj.get("init", {}).get("agent")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", default=None, help="throwaway repo dir (created fresh)")
    parser.add_argument("--log-file", default=None, help="agy --log-file path")
    parser.add_argument(
        "--stdout-file",
        default=None,
        help="where to write agy's raw stdout bytes verbatim (default: alongside --log-file)",
    )
    parser.add_argument("--timeout", type=int, default=150, help="subprocess timeout, seconds")
    args = parser.parse_args()

    tmp_holder = None
    if args.scratch_dir is None:
        tmp_holder = tempfile.mkdtemp(prefix="agy-agent-fallback-capture-")
        scratch_root = pathlib.Path(tmp_holder) / "repo"
    else:
        scratch_root = pathlib.Path(args.scratch_dir)

    log_file = pathlib.Path(args.log_file) if args.log_file else (scratch_root.parent / "agy.log")
    stdout_file = (
        pathlib.Path(args.stdout_file)
        if args.stdout_file
        else (scratch_root.parent / "agent_fallback.ndjson")
    )

    bootstrap_scratch_repo(scratch_root)

    try:
        result = run_capture(scratch_root, log_file, args.timeout)
    except subprocess.TimeoutExpired:
        print("TIMEOUT waiting for agy", file=sys.stderr)
        return 1

    # Persist raw stdout bytes immediately, before any further processing,
    # so a captured run is never lost even if summarization below throws.
    stdout_file.write_bytes(result.stdout)

    print("exit_code:", result.returncode)
    print("stdout_bytes:", len(result.stdout))
    print("stdout_file:", stdout_file)
    print("log_file:", log_file)
    print("requested_agent:", BOGUS_AGENT)
    print("init_agent:", find_init_agent(result.stdout))

    counts = summarize_events(result.stdout)
    print("event_counts:", dict(counts))

    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
