#!/usr/bin/env python3
"""tools/live_stream_events_capture.py — spends real `agy -p` quota to
capture one **event stream** (`--output-format stream-json`, no
`--json-schema`) : **init event** ->
**step update**(xN) -> **result event**.

Sibling to `tools/live_review_capture.py`, and deliberately reuses its
`bootstrap_scratch_repo()` (throwaway scratch git repo + workspace-scoped
`agy-review` agent + one-line seeded bug as an uncommitted diff) and
`PROMPT_TEMPLATE` rather than inventing a new fixture generator — see
docs/json-schema-verdict.md's "Run 3", which used the exact same setup.

NEVER invoked by `make check` or any test in `tests/`. Manual capture only:
run once, persist the raw NDJSON stdout bytes to disk *before* any parsing
(same discipline `live_review_capture.py` follows), then hand-copy the
scrubbed bytes into `tests/fixtures/stream_events/` and record a
PROVENANCE.md row. `companion/stream_events.py`'s tests parse those committed
bytes, never this tool and never a live `agy` run.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import collections
import json
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from live_review_capture import PROMPT_TEMPLATE, bootstrap_scratch_repo  # noqa: E402

import subprocess  # noqa: E402


def run_capture(scratch_root, log_file, timeout, diff_text):
    # --new-project + --agent agy-review, same bind requirement as
    # live_review_capture.py's run_probe — see docs/review-schema-verdict.md
    # Finding B. --json-schema deliberately omitted: out of scope per the
    # epic preamble and docs/json-schema-verdict.md's verdict.
    cmd = [
        "agy", "-p", PROMPT_TEMPLATE.format(diff=diff_text),
        "--disable-slash-commands",
        "--agent", "agy-review",
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
        tmp_holder = tempfile.mkdtemp(prefix="agy-stream-events-capture-")
        scratch_root = pathlib.Path(tmp_holder) / "repo"
    else:
        scratch_root = pathlib.Path(args.scratch_dir)

    log_file = pathlib.Path(args.log_file) if args.log_file else (scratch_root.parent / "agy.log")
    stdout_file = (
        pathlib.Path(args.stdout_file)
        if args.stdout_file
        else (scratch_root.parent / "stream_events.ndjson")
    )

    diff_text = bootstrap_scratch_repo(scratch_root)

    try:
        result = run_capture(scratch_root, log_file, args.timeout, diff_text)
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

    counts = summarize_events(result.stdout)
    print("event_counts:", dict(counts))

    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
