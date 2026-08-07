#!/usr/bin/env python3
"""tools/live_review_capture.py — spends real `agy -p` quota against a
throwaway scratch git repo to probe whether the vendored agy-review custom
agent binds and whether its stdout matches the review JSON schema.

NEVER invoked by `make check` or any other validator. Two uses:

  1. Manual capture (mission M2): pass --scratch-dir under the mission's own
     scratchpad and --log-file under your control; read the printed bind
     proof and, if it bound agy-review, copy the raw stdout into
     tests/fixtures/review/ verbatim before recording a ledger row in
     docs/review-schema-verdict.md.
  2. `make check-live`: re-runs the same probe against whatever `agy` is
     currently on PATH (a future agy release), to re-verify the agent still
     binds and the schema still roughly holds. Exits non-zero on a silent
     fallback or a schema deviation. Requires a real, authenticated `agy`
     and network access — that is exactly why it is opt-in and kept out of
     `make check`.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_SOURCE = REPO_ROOT / "plugins" / "agy" / "agents" / "agy-review" / "agent.md"

# The seeded bug is deliberately tiny and unambiguous (off-by-wrong-
# operator) so a correct review has exactly one obvious finding to surface;
# ceiling: this is a probe fixture generator, not a review-quality corpus.
_SEEDED_GOOD = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'
_SEEDED_BUGGY = 'def add(a, b):\n    """Add two numbers."""\n    return a - b  # bug: should be a + b\n'

PROMPT_TEMPLATE = (
    "Review the following working tree diff for bugs. Output only the JSON "
    "described in your system instructions, nothing else.\n\n"
    "```diff\n{diff}\n```\n"
)
# The diff is embedded directly in the prompt text rather than left
# for the agent to discover via a tool call, because `--sandbox` soft-denies
# tool confirmations in headless print mode (0-byte stdout on exit 0 was
# observed in iteration 1 for exactly this reason); ceiling: fine for a
# single-file probe diff, a real reviewer target would need a different
# diff-sizing strategy (M3's concern, not this probe's).

REQUIRED_KEYS = frozenset(
    ["findings", "overall_correctness", "overall_explanation", "overall_confidence_score"]
)

FALLBACK_RE = re.compile(r'Agent "[^"]+" not found, falling back to default')
CREATED_CONVERSATION_RE = re.compile(r"Created conversation [0-9a-fA-F-]+")


def bootstrap_scratch_repo(root):
    """Create a throwaway git repo at `root` with a workspace-scoped
    agy-review agent and a one-line seeded bug as an uncommitted diff."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "config", "user.email", "scratch@example.invalid"], cwd=str(root), check=True
    )
    subprocess.run(["git", "config", "user.name", "scratch"], cwd=str(root), check=True)

    calc = root / "calc.py"
    calc.write_text(_SEEDED_GOOD, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(root), check=True)

    calc.write_text(_SEEDED_BUGGY, encoding="utf-8")

    agent_dir = root / ".agents" / "agents" / "agy-review"
    agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(AGENT_SOURCE), str(agent_dir / "agent.md"))

    diff = subprocess.run(
        ["git", "diff"], cwd=str(root), check=True, capture_output=True, text=True
    ).stdout
    return diff


def run_probe(scratch_root, log_file, timeout, diff_text):
    # --new-project is required for a workspace-scoped custom agent
    # to bind in `agy -p` print mode (finding B, iteration 2) — every one of
    # the six documented passive placements fails without it. See
    # docs/review-schema-verdict.md for the full evidence.
    cmd = [
        "agy", "-p", PROMPT_TEMPLATE.format(diff=diff_text),
        "--disable-slash-commands",
        "--agent", "agy-review",
        "--sandbox",
        "--new-project",
        "--log-file", str(log_file),
    ]
    # Text=False (raw bytes) so a captured fixture can be written to
    # disk byte-for-byte, exactly as the contract requires — a str round-trip
    # through the platform's default encoding is not guaranteed lossless.
    return subprocess.run(
        cmd,
        cwd=str(scratch_root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=False,
        timeout=timeout,
    )


def bind_proof(log_file):
    """Returns (bound_agy_review: bool, proof_line: str) read back from the
    --log-file. The fallback pattern always wins if present."""
    path = pathlib.Path(log_file)
    if not path.exists():
        return False, "(no log file produced)"
    text = path.read_text(encoding="utf-8", errors="ignore")
    fallback = FALLBACK_RE.search(text)
    if fallback:
        return False, fallback.group(0)
    created = CREATED_CONVERSATION_RE.search(text)
    return True, (created.group(0) if created else "(no 'Created conversation' line found)")


def check_schema(stdout_text):
    """Returns (ok: bool, detail: str)."""
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        return False, "stdout is not valid JSON: {}".format(exc)
    if not isinstance(payload, dict):
        return False, "top-level JSON is not an object"
    missing = REQUIRED_KEYS - set(payload.keys())
    if missing:
        return False, "missing required keys: {}".format(sorted(missing))
    return True, "schema OK"


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
        tmp_holder = tempfile.mkdtemp(prefix="agy-review-check-live-")
        scratch_root = pathlib.Path(tmp_holder) / "repo"
    else:
        scratch_root = pathlib.Path(args.scratch_dir)

    log_file = pathlib.Path(args.log_file) if args.log_file else (scratch_root.parent / "agy.log")
    stdout_file = (
        pathlib.Path(args.stdout_file) if args.stdout_file else (scratch_root.parent / "stdout.raw")
    )

    diff_text = bootstrap_scratch_repo(scratch_root)

    try:
        result = run_probe(scratch_root, log_file, args.timeout, diff_text)
    except subprocess.TimeoutExpired:
        print("TIMEOUT waiting for agy", file=sys.stderr)
        return 1

    # Persist raw stdout bytes immediately, before any further processing,
    # so a captured run is never lost even if schema checking below throws.
    stdout_file.write_bytes(result.stdout)

    print("exit_code:", result.returncode)
    bound, proof = bind_proof(log_file)
    print("bind_proof_line:", proof)
    print("bound_agy_review:", bound)
    print("stdout_bytes:", len(result.stdout))
    print("stdout_file:", stdout_file)

    if not bound:
        print(
            "SILENT FALLBACK: this run's stdout is NOT a valid fixture.",
            file=sys.stderr,
        )
        return 1

    stdout_text = result.stdout.decode("utf-8", errors="replace")
    ok, detail = check_schema(stdout_text)
    print("schema_check:", detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
