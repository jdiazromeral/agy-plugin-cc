#!/usr/bin/env python3
"""tools/live_session_hook_capture.py — drives a REAL, non-interactive Claude
Code session (`claude -p --output-format stream-json`) to measure whether the
`AGY_COMPANION_SESSION_ID` exported by this plugin's SessionStart hook
(`plugins/agy/scripts/hooks/session_start.py` -> `$CLAUDE_ENV_FILE`) actually
reaches a **companion** subprocess.

Spends CLAUDE tokens only. It NEVER invokes `agy`, directly or indirectly:
both probes are launched with `--allowedTools 'Bash(python3:*)'`, so the only
command the captured session can run without a permission prompt is a
`python3` one, and `--permission-prompts none` denies everything else
automatically instead of blocking on a prompt. `/agy:status`'s companion path
(`companion/status.py`) is documented and verified read-only: "never touches
`agy` or spawns anything".

Two probes, run in one fresh throwaway scratch root:

  probe 1 `agy_status` — prompt is literally `/agy:status`, i.e. the real
      production invocation (`plugins/agy/commands/status.md` runs
      `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agy_companion.py" status`).
      Before the run, the scratch repo's **state dir** is seeded with two
      **job** records: one whose `session_id` is exactly the UUID pinned with
      `--session-id`, one whose `session_id` is a sentinel that cannot match.
      `status.run()` scopes the default table by `AGY_COMPANION_SESSION_ID`,
      so which of the two rows comes back is an exact-value proof of what the
      companion subprocess saw in its own environment.
  probe 2 `env_value` — prints the literal value from inside a `python3`
      subprocess, importing `companion.state` from the very plugin directory
      probe 1 measured, so the raw bytes are recorded and nothing has to be
      inferred from the scoping behaviour alone.

Which plugin directory the captured run loaded is MEASURED, never assumed,
from two independent places in the captured stream: the `init` system event's
`plugins[]` entry for `agy` (`path`, `version`), and the expanded
`${CLAUDE_PLUGIN_ROOT}` inside the `tool_use` block's `command` string.

NEVER invoked by `make check` or any test in `tests/`. Manual capture only:
run once, persist the raw stdout/stderr bytes to disk before any parsing,
then hand-copy the scrubbed bytes into `tests/fixtures/session_hook/` and
write its PROVENANCE.md — the same discipline
`tools/live_stream_events_capture.py` follows.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# A value no real Claude Code session id can equal, so a row carrying it can
# only ever show up when the companion saw NO session id at all.
SENTINEL_SESSION_ID = "SENTINEL-NOT-THE-CAPTURED-SESSION"
MATCH_JOB_ID = "seeded-match-job"
OTHER_JOB_ID = "seeded-other-job"

# Printed by probe 2 so the literal value is greppable in the raw bytes.
ENV_PROBE_MARKER = "MEASURED_ENV"


def assert_throwaway(scratch_root):
    """Refuse to spend a captured run from a cwd that is not a fresh
    throwaway directory under the system temp dir.

    AGENTS.md's Never section: "Never spend a live run from a `cwd` the tool
    has not asserted is a throwaway. A capture tool must check its own
    working directory is under a temp dir and fail loudly otherwise, rather
    than trusting its caller to pass the right `cwd=`." Called BEFORE any
    `claude` process is launched, so a bad `--scratch-dir` costs nothing.
    """
    resolved = pathlib.Path(scratch_root).resolve()
    tmp_root = pathlib.Path(tempfile.gettempdir()).resolve()
    if resolved == tmp_root or tmp_root not in resolved.parents:
        raise SystemExit(
            "refusing to capture: scratch root {} is not under the system temp "
            "dir {}".format(resolved, tmp_root)
        )
    if resolved == REPO_ROOT or REPO_ROOT == resolved or REPO_ROOT in resolved.parents:
        raise SystemExit(
            "refusing to capture: scratch root {} is inside this repo {}".format(
                resolved, REPO_ROOT
            )
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise SystemExit(
            "refusing to capture: scratch root {} already exists and is not "
            "empty; this tool creates its own fresh directory".format(resolved)
        )
    return resolved


def bootstrap_scratch(scratch_root):
    """Fresh throwaway git repo + fresh `$CLAUDE_PLUGIN_DATA`. The repo must
    be a real git repo because `companion.git.ensure_git_repository` resolves
    the repo root before `status` reads anything."""
    repo = scratch_root / "repo"
    plugin_data = scratch_root / "plugin-data"
    repo.mkdir(parents=True)
    plugin_data.mkdir(parents=True)
    for argv in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "capture@example.invalid"],
        ["git", "config", "user.name", "capture"],
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(argv, cwd=str(repo), stdin=subprocess.DEVNULL, check=True)
    return repo, plugin_data


def seed_jobs(repo, plugin_data, session_id):
    """Seed the scratch repo's **state dir** with the two discriminator
    **job** records. Both are `completed`, never `running`: a running job
    whose session differs and whose pid probes alive derives as an
    **orphan**, and `status._scope_to_session` deliberately keeps orphans
    visible in the session-scoped view — which would destroy the
    discriminator."""
    scripts_dir = REPO_ROOT / "plugins" / "agy" / "scripts"
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(plugin_data))
    code = (
        "import sys; sys.path.insert(0, {scripts!r})\n"
        "from companion import state\n"
        "state.upsert_job({repo!r}, {{'id': {match!r}, 'kind': 'review',"
        " 'status': 'completed', 'session_id': {sid!r}}})\n"
        "state.upsert_job({repo!r}, {{'id': {other!r}, 'kind': 'review',"
        " 'status': 'completed', 'session_id': {sentinel!r}}})\n"
        "print(state.resolve_state_file({repo!r}))\n"
    ).format(
        scripts=str(scripts_dir),
        repo=str(repo),
        match=MATCH_JOB_ID,
        other=OTHER_JOB_ID,
        sid=session_id,
        sentinel=SENTINEL_SESSION_ID,
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def run_probe(prompt, session_id, repo, plugin_data, out_prefix, timeout):
    """One real `claude -p` run. Raw stdout/stderr bytes are written to disk
    immediately, before any parsing, so a spent run is never lost.

    No `--dangerously-skip-permissions` / `--permission-mode
    bypassPermissions` anywhere: `--allowedTools 'Bash(python3:*)'` is the
    narrowest grant that lets the run observe the variable (it matches
    `plugins/agy/commands/status.md`'s own `allowed-tools` frontmatter
    exactly), and `--permission-prompts none` makes the run unable to hang on
    an unanswered prompt — anything outside the grant is denied instead.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--session-id",
        session_id,
        "--allowedTools",
        "Bash(python3:*)",
        "--permission-prompts",
        "none",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(plugin_data))
    result = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=False,
        timeout=timeout,
    )
    stdout_file = pathlib.Path("{}.stream.ndjson".format(out_prefix))
    stderr_file = pathlib.Path("{}.stderr.txt".format(out_prefix))
    stdout_file.write_bytes(result.stdout)
    stderr_file.write_bytes(result.stderr)
    return result, stdout_file, stderr_file


def iter_events(stdout_bytes):
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def loaded_plugin_path(events):
    """The agy plugin directory the captured run actually loaded, read off
    the `init` system event's own `plugins[]` table. Measured, not assumed —
    in particular it is what distinguishes the live directory-marketplace
    source from the stale version-keyed install cache."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            for plugin in event.get("plugins") or []:
                if plugin.get("name") == "agy":
                    return plugin.get("path"), plugin.get("version")
    return None, None


def session_start_hook_outcome(events):
    """The SessionStart hook's own `hook_response` system event, if the
    captured run emitted one — the independent signal that separates "the
    hook never ran" from "the hook ran but the variable did not arrive"."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "hook_response":
            if event.get("hook_event") == "SessionStart":
                return {
                    "hook_name": event.get("hook_name"),
                    "exit_code": event.get("exit_code"),
                    "outcome": event.get("outcome"),
                    "stderr": event.get("stderr"),
                }
    return None


def tool_calls(events):
    """(command, result_text) pairs for every Bash tool call in the run."""
    commands = []
    results = []
    for event in events:
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                commands.append((block.get("input") or {}).get("command"))
            elif block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                results.append(content)
    return commands, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="throwaway capture root (created fresh; must be under the system temp dir)",
    )
    parser.add_argument("--timeout", type=int, default=300, help="per-probe timeout, seconds")
    args = parser.parse_args()

    if args.scratch_dir is None:
        scratch_root = pathlib.Path(tempfile.mkdtemp(prefix="agy-session-hook-capture-"))
    else:
        scratch_root = pathlib.Path(args.scratch_dir)
    # Asserted BEFORE anything is spent, per AGENTS.md's Never rule.
    scratch_root = assert_throwaway(scratch_root)

    repo, plugin_data = bootstrap_scratch(scratch_root)
    probe1_session = str(uuid.uuid4())
    probe2_session = str(uuid.uuid4())
    state_file = seed_jobs(repo, plugin_data, probe1_session)

    print("scratch_root:", scratch_root)
    print("repo:", repo)
    print("state_file:", state_file)
    print("probe1_session_id:", probe1_session)
    print("probe2_session_id:", probe2_session)
    print("seeded_jobs: {} (session_id == probe1_session_id), {} (session_id == {!r})".format(
        MATCH_JOB_ID, OTHER_JOB_ID, SENTINEL_SESSION_ID
    ))
    print()

    # --- probe 1: the real production invocation -----------------------------
    result1, stdout1, stderr1 = run_probe(
        "/agy:status",
        probe1_session,
        repo,
        plugin_data,
        str(scratch_root / "probe1-agy-status"),
        args.timeout,
    )
    events1 = list(iter_events(result1.stdout))
    plugin_path, plugin_version = loaded_plugin_path(events1)
    hook = session_start_hook_outcome(events1)
    commands1, results1 = tool_calls(events1)
    table = "\n".join(text or "" for text in results1)

    print("probe1 exit_code:", result1.returncode)
    print("probe1 stdout_file:", stdout1)
    print("probe1 stderr_file:", stderr1)
    print("PLUGIN DIRECTORY THE CAPTURED RUN LOADED (measured, not assumed):")
    print("  init event plugins[agy].path    :", plugin_path)
    print("  init event plugins[agy].version :", plugin_version)
    for command in commands1:
        print("  expanded ${CLAUDE_PLUGIN_ROOT} in tool_use command:", command)
    print("  NOTE: this is the marketplace-registered install, which is NOT this")
    print("        repo checkout unless the two paths above are this checkout:", REPO_ROOT)
    print("SessionStart hook_response event:", json.dumps(hook))
    print()

    saw_match = MATCH_JOB_ID in table
    saw_other = OTHER_JOB_ID in table
    scoped_empty_note = "No agy jobs for the current session in this repo yet." in table
    print("probe1 companion output rows: match={} other={} empty_scoped_note={}".format(
        saw_match, saw_other, scoped_empty_note
    ))

    # --- probe 2: the literal value, from the same plugin directory ----------
    scripts_dir = (
        str(pathlib.Path(plugin_path) / "scripts")
        if plugin_path
        else str(REPO_ROOT / "plugins" / "agy" / "scripts")
    )
    inner = (
        'import os, sys; sys.path.insert(0, "{scripts}"); from companion import state; '
        'print("{marker} " + state.SESSION_ID_ENV + "=" + '
        "repr(os.environ.get(state.SESSION_ID_ENV)))"
    ).format(scripts=scripts_dir, marker=ENV_PROBE_MARKER)
    prompt2 = (
        "Run exactly this one bash command, run nothing else, and then reply "
        "with its stdout verbatim and nothing else:\n\n"
        "python3 -c '{}'\n".format(inner)
    )
    result2, stdout2, stderr2 = run_probe(
        prompt2,
        probe2_session,
        repo,
        plugin_data,
        str(scratch_root / "probe2-env-value"),
        args.timeout,
    )
    events2 = list(iter_events(result2.stdout))
    _, results2 = tool_calls(events2)
    measured_lines = [
        line
        for text in results2
        for line in (text or "").splitlines()
        if line.startswith(ENV_PROBE_MARKER)
    ]
    print()
    print("probe2 exit_code:", result2.returncode)
    print("probe2 stdout_file:", stdout2)
    print("probe2 stderr_file:", stderr2)
    print("probe2 scripts_dir under test:", scripts_dir)
    for line in measured_lines:
        print("probe2 captured bytes:", line)

    # --- verdict -------------------------------------------------------------
    print()
    reaches_companion = saw_match and not saw_other
    value_matches = any(probe2_session in line for line in measured_lines)
    if reaches_companion and value_matches:
        print(
            "PASS: AGY_COMPANION_SESSION_ID reaches a companion subprocess. "
            "Probe 1's session-scoped table showed only the job whose session_id "
            "equals the pinned --session-id, and probe 2 printed the literal value."
        )
        return 0
    print(
        "FAIL: reaches_companion={} value_matches={} — read the raw bytes in {} "
        "before concluding anything.".format(reaches_companion, value_matches, scratch_root)
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
