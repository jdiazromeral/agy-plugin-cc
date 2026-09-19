#!/usr/bin/env python3
"""tools/live_session_env_conditions_capture.py — M3 proved that
`AGY_COMPANION_SESSION_ID` DOES reach a **companion** subprocess in a
headless `claude -p` run with a pinned `--session-id`. This tool asks the
next question by capture rather than by inference: **which conditions
produce a present vs. an absent value?**

Two conditions are reachable without a TTY, and those are the only two this
tool claims anything about:

  probe 1 `fresh_unpinned` — a fresh `claude -p` run with **no**
      `--session-id` at all (Claude Code generates one). M3 always pinned
      the id, so "does the export depend on the caller pinning the session
      id?" was open. The run's own generated id is read off the `init`
      event, so the printed value can be compared against it exactly.
  probe 2 `resume` — `claude -p --resume <probe 1's session id>` in the same
      cwd. This is the `SessionStart` **source** `resume` rather than
      `startup`; `plugins/agy/hooks/hooks.json` registers `SessionStart`
      with no matcher, so the hook is *configured* to fire for every source,
      but whether it does — and whether the exported variable survives into
      a resumed session's tool subprocesses — had never been measured.
  probe 3 `subagent` — one fresh run that measures the variable TWICE: once
      from a `Bash` tool call in the main thread, and once from a `Bash`
      tool call made by a `Task` subagent of that same session. Both
      measurements come from a single session, so the two values cannot be
      confounded by session age, binary version or plugin install state.
      This is the only probe that can separate "the export works" from "the
      export works but does not reach a subagent's tool subprocess", and a
      subagent is where the absence was actually observed.

**Not reachable from here, and therefore NOT measured by this tool:** a
genuinely interactive TTY session (the condition in which the epic runner
observed the variable absent). A subagent cannot drive one. Its result is
written as "not tested" in the fixture's PROVENANCE.md and must never be
reframed as a finding — see AGENTS.md's Never section.

Spends CLAUDE tokens only, and NEVER invokes `agy`: it reuses
`live_session_hook_capture.run_probe`, whose `--allowedTools
'Bash(python3:*)'` grant plus `--permission-prompts none` make a non-python3
command impossible to run and impossible to hang on. The only command either
probe is asked to run is a one-line `python3 -c` that prints an environment
variable.

Unlike M3's probe 2, the inner command does **not** import
`companion.state` to obtain the variable's name: the plugin directory is not
known until the `init` event of the very run being launched, and probe 1
deliberately has no prior run to read it from. The literal name
`AGY_COMPANION_SESSION_ID` is used instead, and the plugin directory that
actually loaded is still measured, independently, from each run's `init`
event `plugins[]` table.

NEVER invoked by `make check` or any test in `tests/`. Manual capture only.

Standard library only. Python 3.9-compatible syntax.
"""
import argparse
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from live_session_hook_capture import (  # noqa: E402
    ENV_PROBE_MARKER,
    assert_throwaway,
    bootstrap_scratch,
    iter_events,
    loaded_plugin_path,
    run_probe,
    tool_calls,
)

ENV_VAR = "AGY_COMPANION_SESSION_ID"

INNER_COMMAND = (
    'python3 -c \'import os; print("{marker} {var}=" + '
    'repr(os.environ.get("{var}")))\''
).format(marker=ENV_PROBE_MARKER, var=ENV_VAR)

PROMPT = (
    "Run exactly this one bash command, run nothing else, and then reply "
    "with its stdout verbatim and nothing else:\n\n{}\n".format(INNER_COMMAND)
)

# Probe 3 tags the two measurements apart at the source, so the captured
# bytes themselves say which thread produced which value — nothing has to be
# inferred from the order the tool_result blocks happen to arrive in.
MAIN_MARKER = "MAIN"
SUBAGENT_MARKER = "SUBAGENT"


def _tagged_command(tag):
    return (
        'python3 -c \'import os; print("{marker} {tag} {var}=" + '
        'repr(os.environ.get("{var}")))\''
    ).format(marker=ENV_PROBE_MARKER, tag=tag, var=ENV_VAR)


SUBAGENT_PROMPT = (
    "Do exactly these two steps in order, and nothing else.\n\n"
    "Step 1. Run this bash command yourself, in this main thread:\n\n"
    "{main}\n\n"
    "Step 2. Use the Task tool to launch ONE general-purpose subagent. Its "
    "entire task is to run this bash command and report its stdout "
    "verbatim:\n\n"
    "{sub}\n\n"
    "Then reply with both stdout lines verbatim, one per line, and nothing "
    "else.\n".format(main=_tagged_command(MAIN_MARKER), sub=_tagged_command(SUBAGENT_MARKER))
)

# Probe 3 needs the Task tool on top of the python3 Bash grant. Still no
# `--dangerously-skip-permissions` and still `--permission-prompts none`
# (added by run_probe), so nothing outside these two tools can run and
# nothing can hang on a prompt. `agy` remains unreachable: the Bash grant is
# `python3` only, on the main thread and inside the subagent alike.
SUBAGENT_ALLOWED_TOOLS = "Bash(python3:*),Task"


def session_start_hooks(events):
    """EVERY `SessionStart` hook event in the stream, not just the first —
    the `hook_name` carries the source (`SessionStart:startup` vs
    `SessionStart:resume`), which is exactly the thing under test here, so
    a probe that fired none must be distinguishable from one that fired a
    differently-sourced one."""
    found = []
    for event in events:
        if event.get("type") != "system":
            continue
        if event.get("hook_event") != "SessionStart":
            continue
        found.append({
            "subtype": event.get("subtype"),
            "hook_name": event.get("hook_name"),
            "exit_code": event.get("exit_code"),
            "outcome": event.get("outcome"),
            "stderr": event.get("stderr"),
        })
    return found


def init_session_id(events):
    """The session id Claude Code itself reports for the run, read off the
    `init` system event. For probe 1 (no `--session-id` passed) this is the
    only way to learn the generated id — and it is what probe 2 resumes."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("session_id")
    return None


def measured_values(events):
    """The `MEASURED_ENV ...` lines the probe's own `python3` subprocess
    printed, taken from the `tool_result` bytes."""
    _, results = tool_calls(events)
    return [
        line
        for text in results
        for line in (text or "").splitlines()
        if line.startswith(ENV_PROBE_MARKER)
    ]


def report(label, result, events, stdout_file, stderr_file):
    plugin_path, plugin_version = loaded_plugin_path(events)
    hooks = session_start_hooks(events)
    values = measured_values(events)
    print("--- probe {} ---".format(label))
    print("exit_code:", result.returncode)
    print("stdout_file:", stdout_file)
    print("stderr_file:", stderr_file)
    print("init session_id:", init_session_id(events))
    print("init plugins[agy].path:", plugin_path)
    print("init plugins[agy].version:", plugin_version)
    print("SessionStart hook events:", json.dumps(hooks))
    for value in values:
        print("captured bytes:", value)
    if not values:
        print("captured bytes: (none — no MEASURED_ENV line in any tool_result)")
    print()
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="fresh_unpinned,resume,subagent",
        help="comma-separated probe names to run (default: all three). Probe "
             "2 needs probe 1's generated session id, so 'resume' alone is "
             "refused rather than silently resuming something else.",
    )
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="throwaway capture root (created fresh; must be under the system temp dir)",
    )
    parser.add_argument("--timeout", type=int, default=300, help="per-probe timeout, seconds")
    args = parser.parse_args()

    if args.scratch_dir is None:
        scratch_root = pathlib.Path(tempfile.mkdtemp(prefix="agy-session-env-conditions-"))
    else:
        scratch_root = pathlib.Path(args.scratch_dir)
    # Asserted BEFORE anything is spent, per AGENTS.md's Never rule.
    scratch_root = assert_throwaway(scratch_root)

    selected = [name.strip() for name in args.only.split(",") if name.strip()]
    unknown = set(selected) - {"fresh_unpinned", "resume", "subagent"}
    if unknown:
        raise SystemExit("unknown probe name(s): {}".format(sorted(unknown)))
    if "resume" in selected and "fresh_unpinned" not in selected:
        raise SystemExit(
            "probe 'resume' resumes the session probe 'fresh_unpinned' creates; "
            "run them together or not at all"
        )

    repo, plugin_data = bootstrap_scratch(scratch_root)
    print("scratch_root:", scratch_root)
    print("repo:", repo)
    print("probes selected:", selected)
    print("inner command probes 1 and 2 run:", INNER_COMMAND)
    print()

    if "subagent" in selected:
        result3, stdout3, stderr3 = run_probe(
            SUBAGENT_PROMPT,
            None,
            repo,
            plugin_data,
            str(scratch_root / "probe3-subagent"),
            args.timeout,
            session_args=[],
            allowed_tools=SUBAGENT_ALLOWED_TOOLS,
        )
        events3 = list(iter_events(result3.stdout))
        values3 = report("3 subagent", result3, events3, stdout3, stderr3)
        main_lines = [line for line in values3 if MAIN_MARKER in line]
        sub_lines = [line for line in values3 if SUBAGENT_MARKER in line]
        print("probe 3 main-thread lines    :", main_lines or "(none)")
        print("probe 3 subagent-thread lines:", sub_lines or "(none)")
        if not sub_lines:
            print(
                "probe 3: NO subagent line was captured. That is 'not tested', "
                "NOT evidence the variable is absent in a subagent — the Task "
                "tool may simply not have run. Read the raw bytes in {} before "
                "writing anything down.".format(scratch_root)
            )
        print()

    if "fresh_unpinned" not in selected:
        return 0

    # --- probe 1: fresh session, NO --session-id pinned ----------------------
    result1, stdout1, stderr1 = run_probe(
        PROMPT,
        None,
        repo,
        plugin_data,
        str(scratch_root / "probe1-fresh-unpinned"),
        args.timeout,
        session_args=[],
    )
    events1 = list(iter_events(result1.stdout))
    values1 = report("1 fresh_unpinned", result1, events1, stdout1, stderr1)
    generated_session = init_session_id(events1)

    if not generated_session:
        print(
            "ABORT: probe 1 emitted no init session_id, so there is nothing to "
            "resume. Read the raw bytes in {} before concluding anything.".format(
                scratch_root
            )
        )
        return 1

    if "resume" not in selected:
        return 0

    # --- probe 2: resume THAT session ---------------------------------------
    result2, stdout2, stderr2 = run_probe(
        PROMPT,
        None,
        repo,
        plugin_data,
        str(scratch_root / "probe2-resume"),
        args.timeout,
        session_args=["--resume", generated_session],
    )
    events2 = list(iter_events(result2.stdout))
    values2 = report("2 resume", result2, events2, stdout2, stderr2)

    # --- verdict: report what was measured, decide nothing else -------------
    print("=== measured ===")
    print("probe 1 (fresh, unpinned) generated session_id:", generated_session)
    print("probe 1 value present and equal to that id:",
          any(generated_session in line for line in values1))
    print("probe 2 (resume) resumed session_id:", init_session_id(events2))
    print("probe 2 value present and equal to probe 1's id:",
          any(generated_session in line for line in values2))
    print()
    print("NOT TESTED by this tool: an interactive TTY session. A subagent")
    print("cannot drive one; do not infer anything about it from the above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
