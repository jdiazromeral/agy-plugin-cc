"""companion.review — prompt assembly, sizing consumption, and the
foreground live path for `/agy:review`.

Consumes a **review target** resolved by `companion.git` (working tree,
staged changes, or a branch diff against a base ref) and its size (files
touched, insertions, deletions — also from `companion.git`), assembles the
prompt sent to `agy`, and owns the diff/manifest text (`_diff_text`,
`_tracked_files`) that goes into that prompt. `--dry-run` previews the
prompt without invoking `agy`; without it, `run()` launches `agy` once as a
blocking foreground run bound to the vendored `agy-review` agent (see
`plugins/agy/agents/agy-review/agent.md`), verifies the run actually
**bound** that agent by reading its `--log-file` (never trusting stdout
first — a **silent fallback** gives no other signal), and renders the
**tolerant parse** of the review JSON pulled from the **event stream**'s
**result event** `response` field (via `companion.stream_events`) — never
from agy's raw stdout directly, though a stream that fails to parse, or
that never reaches a **result event**, falls back to the raw stdout text
so `tolerant_parse` still gets a try at it. The live launch mirrors
`tools/live_review_capture.py`'s proven `agy -p ... --disable-slash-commands
--agent agy-review --sandbox --new-project --output-format stream-json
--log-file <path>` invocation exactly.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from companion import agent_workspace, state, stream_events
from companion import repo as repo_arg
from companion.agent_workspace import AgentWorkspaceError
from companion.agy_log import _bind_check
from companion.git import (
    GitTargetError,
    _git_output,
    _lines,
    ensure_git_repository,
    resolve_target,
    target_size,
)
from companion.launch import (
    _AGY_TIMEOUT_SECONDS,
    _BACKGROUND_PRINT_TIMEOUT_ARG,
    _print_timeout_arg,
    _spawn_detached,
    positive_int_timeout,
)
from companion.review_output import render_review, tolerant_parse

HELP = "Resolve a review target, size the change, and either preview the prompt (--dry-run) or run a live agy review."

_SCOPES = ("auto", "working-tree", "staged", "branch")

# The diff is embedded directly in the prompt text rather than left
# for agy-review to discover via a tool call, because `--sandbox` soft-denies
# tool confirmations in headless print mode (see tools/live_review_capture.py
# and docs/review-schema-verdict.md, Finding C) — mirrors the exact prompt
# shape M2's live captures proved binds and returns schema-valid JSON.
_REVIEW_INSTRUCTIONS_TEMPLATE = (
    "Review the following diff for bugs. Output only the JSON described in "
    "your system instructions, nothing else.\n\n"
    "```diff\n{diff}\n```\n"
)

_DEFAULT_PROMPT_TEMPLATE = (
    "Review target: {label}\n"
    "Files touched: {files}\n"
    "Insertions: +{insertions}\n"
    "Deletions: -{deletions}\n"
    "{manifest_section}"
    "\n"
    "{review_instructions}\n"
)

_AGY_AGENT_NAME = "agy-review"

# Cap on how many tracked file paths the **manifest** (see
# assemble_prompt / _format_manifest) embeds in the prompt. An unbounded
# `git ls-files` on a large repo could blow the prompt/token budget; a
# reader looking for "why did my repo's file list get cut off" finds the
# answer here, and the cutoff itself is always stated in the rendered
# prompt text (never a silent drop) — see _format_manifest.
_MANIFEST_MAX_FILES = 300


class AgyLaunchError(Exception):
    """The foreground `agy` launch could not even start (binary missing,
    timed out) — distinct from a bound-but-crashed or silent-fallback run,
    both of which do produce a `--log-file` to reason about."""


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and size the review target, and print the prompt that would be sent to "
        "agy, without invoking it. Without this flag, review runs the live foreground path.",
    )
    parser.add_argument(
        "--scope",
        choices=list(_SCOPES),
        default="auto",
        help="Which review target to resolve: auto (default), working-tree, staged, or branch.",
    )
    parser.add_argument(
        "--base",
        default=None,
        metavar="REF",
        help="Base ref for a branch diff. Implies branch mode regardless of --scope.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON object instead of the human report.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Launch agy detached and return immediately, printing the job id instead of "
        "waiting for and rendering the review. Check progress with /agy:status.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int_timeout,
        default=None,
        metavar="SECONDS",
        help="Foreground subprocess timeout in seconds (default: {}). Rejected together "
        "with --background, which has no timeout ceiling by design. Not forwarded unless "
        "supplied.".format(_AGY_TIMEOUT_SECONDS),
    )


def run(args):
    if args.timeout is not None and args.background:
        print(
            "error: --timeout cannot be combined with --background: background runs have "
            "no timeout ceiling by design.",
            file=sys.stderr,
        )
        return 1

    cwd = repo_arg.resolve_repo(args)
    try:
        target = resolve_target(cwd, args.scope, args.base)
        size = target_size(cwd, target)
        diff_text = _diff_text(cwd, target)
        manifest = _tracked_files(cwd)
    except GitTargetError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    review_instructions = _REVIEW_INSTRUCTIONS_TEMPLATE.format(diff=diff_text)
    prompt = assemble_prompt(
        target, size, review_instructions=review_instructions, manifest=manifest
    )
    nothing_to_review = size["files"] == 0

    if args.dry_run:
        payload = {
            "target": target,
            "size": size,
            "nothing_to_review": nothing_to_review,
            "prompt": prompt,
        }
        if args.json:
            print(json.dumps(payload))
        else:
            print(_render_human(payload))
        return 0

    if nothing_to_review:
        print("Nothing to review: {} is empty.".format(target["label"]))
        return 0

    repo_root = ensure_git_repository(cwd)
    if args.background:
        return _run_background_review(repo_root, prompt)
    timeout = args.timeout if args.timeout is not None else _AGY_TIMEOUT_SECONDS
    return _run_live_review(repo_root, prompt, args.json, timeout=timeout)


# --- foreground live launch ------------------------------------------------


def _run_live_review(repo_root, prompt, as_json, agent_name=_AGY_AGENT_NAME, timeout=_AGY_TIMEOUT_SECONDS):
    """Launch `agy` once as a blocking foreground run, verify it **bound**
    `agent_name` (defaults to the `agy-review` agent; `/agy:adversarial-review`
    passes `agy-adversarial-review` instead), then render the **tolerant
    parse** of its output. Returns the process exit code."""
    try:
        bind_state, bind_proof, returncode, stdout_bytes, stderr_bytes = _launch_agy(
            repo_root, prompt, agent_name=agent_name, timeout=timeout
        )
    except (AgyLaunchError, AgentWorkspaceError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    if bind_state == "not_bound":
        print(
            "error: SILENT FALLBACK — agy did not bind the \"{}\" agent (log: {}); "
            "its output is not a review.".format(agent_name, bind_proof),
            file=sys.stderr,
        )
        return 1

    if bind_state == "unknown":
        # Fail closed: an `unknown` bind is not evidence of a genuine bind
        # (that was the bug — see _bind_check's docstring), so it is treated
        # at least as cautiously as a confirmed silent fallback. agy's
        # stdout is never rendered as a trusted review on zero evidence.
        print(
            "error: UNCONFIRMED BIND — could not verify agy bound the \"{}\" agent "
            "(log: {}); its output is not a review.".format(agent_name, bind_proof),
            file=sys.stderr,
        )
        return 1

    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    if returncode != 0 and not stdout_text.strip():
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        print(
            "error: agy bound the \"{}\" agent but exited {} with no output: {}".format(
                agent_name, returncode, stderr_text or "(no stderr)"
            ),
            file=sys.stderr,
        )
        return 1

    review_text = _extract_review_text(stdout_text)
    parsed = tolerant_parse(review_text)
    if as_json and parsed["ok"]:
        print(json.dumps(parsed))
    else:
        print(render_review(parsed))
    return 0


def _extract_review_text(stdout_text):
    """Pull the review JSON to hand to `tolerant_parse` out of `stdout_text`
    — `agy`'s raw stdout under `--output-format stream-json` (an **event
    stream**: NDJSON, `init` -> `step_update`(xN) -> `result`). Returns the
    **result event**'s `response` field when one is cleanly reachable, and
    `stdout_text` itself, UNCHANGED, otherwise — a stream that fails to
    parse at all, or one that never reaches a **result event** (e.g. a
    mid-stream crash), must never raise or silently lose output; falling
    back to the raw text lets `tolerant_parse` have a try at it instead, per
    AGENTS.md's "a review that renders ugly beats a review that vanishes".

    `stream_events.parse_event_stream` (companion.stream_events) has no
    try/except of its own around each line's `json.loads`, by design — the
    guard belongs at the consumer, which knows what to fall back to.
    Catching `ValueError` (which `json.JSONDecodeError` subclasses) around
    the parse call is enough."""
    try:
        event_stream = stream_events.parse_event_stream(stdout_text)
    except ValueError:
        return stdout_text
    if event_stream.response is None:
        return stdout_text
    return event_stream.response


def _launch_agy(repo_root, prompt, agent_name=_AGY_AGENT_NAME, timeout=_AGY_TIMEOUT_SECONDS):
    """Launch `agy -p <prompt> --disable-slash-commands --agent <agent_name>
    --sandbox --new-project --output-format stream-json --log-file
    <ephemeral temp path> --add-dir <repo_root>`, blocking.
    Returns (bind_state, bind_proof, returncode, stdout_bytes, stderr_bytes)
    where bind_state is one of "bound", "not_bound", "unknown" (see
    _bind_check). Raises AgyLaunchError if agy itself cannot be found or
    times out.

    cwd is the **agent workspace**, not `repo_root`: agy only resolves a
    workspace-scoped agent from its own cwd, so running in the repo can never
    bind the vendored agent (see companion.agent_workspace). The repo is
    attached with `--add-dir` instead — nothing is written into it.

    No persistent state dir — the --log-file is an ephemeral temp file for
    this one foreground run only (a per-repo state dir is M5's job)."""
    with tempfile.TemporaryDirectory(prefix="agy-review-") as tmp, \
            agent_workspace.ephemeral(agent_name) as workspace:
        log_file = Path(tmp) / "agy.log"
        cmd = _agy_command(
            prompt, log_file, agent_name=agent_name, add_dir=repo_root,
            print_timeout=_print_timeout_arg(timeout),
        )
        try:
            # Text=False (raw bytes) so a real fixture-shaped
            # response is decoded exactly once, deliberately, in
            # _run_live_review — a str round-trip through the platform
            # default encoding here is not guaranteed lossless.
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=False,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise AgyLaunchError(
                "agy is not installed or not on PATH. Run `/agy:setup` to check."
            )
        except subprocess.TimeoutExpired:
            raise AgyLaunchError(
                "agy timed out after {}s".format(timeout)
            )

        log_text = log_file.read_text(encoding="utf-8", errors="ignore") if log_file.exists() else ""
        bind_state, bind_proof = _bind_check(log_text)
        return bind_state, bind_proof, result.returncode, result.stdout, result.stderr


def _agy_command(prompt, log_file, agent_name=_AGY_AGENT_NAME, add_dir=None, print_timeout=None):
    """Build the `agy` command vector shared by both the foreground and
    **background launch** paths — only the `--log-file` target differs
    (an ephemeral temp path for the foreground run, the job's persistent
    path under the **state dir** for a background one). `agent_name`
    defaults to `agy-review`; `/agy:adversarial-review` calls this with
    `agent_name="agy-adversarial-review"` instead of forking this function.
    Pure — no I/O — so it is trivial to assert on directly in a test.

    `add_dir` attaches the reviewed repo to a run whose cwd is the **agent
    workspace** rather than the repo itself (see companion.agent_workspace).
    Optional only so the pure-command tests can call this without one; every
    real launch passes the repo root.

    `print_timeout`, when given, is appended as `--print-timeout
    <print_timeout>` right after `--log-file` — the pre-computed argument
    STRING (see `companion.launch._print_timeout_arg` for the foreground
    derivation and `_BACKGROUND_PRINT_TIMEOUT_ARG` for the background
    constant), never a bare number. `None` (the default) omits the flag, so
    a bare direct call — as this module's own pure-vector tests make — is
    unaffected; every real launch (`_launch_agy`, `_run_background_review`)
    always supplies one.

    `prompt` embeds verbatim user text (a diff, and for
    `/agy:adversarial-review`, a free-text focus), so
    `--disable-slash-commands` is always present — agy resolves slash
    commands in print mode from 1.1.9 on, and every supported binary is
    above that (`setup.MIN_AGY_VERSION`), so a prompt beginning with `/`
    would otherwise be resolved as **slash-command expansion** instead of
    being sent as literal text. Unconditional, no version check."""
    cmd = [
        "agy", "-p", prompt,
        "--disable-slash-commands",
        "--agent", agent_name,
        "--sandbox",
        "--new-project",
        "--output-format", "stream-json",
        "--log-file", str(log_file),
    ]
    if print_timeout:
        cmd += ["--print-timeout", print_timeout]
    if add_dir is not None:
        cmd += ["--add-dir", str(add_dir)]
    return cmd


# --- background launch ------------------------------------------------------


def _run_background_review(repo_root, prompt, spawn=_spawn_detached, agent_name=_AGY_AGENT_NAME):
    """Launch `agy` detached, writing its output to a persistent
    `--log-file` under the repo's **state dir**, and record the **job**
    immediately — without blocking on the child. `/agy:status` (a separate
    turn, a separate process) is the only thing that ever reads that log
    again; this function never waits for or inspects it. The job's stdout
    is captured to a persistent `output_file` (see state.py) so `/agy:result`
    can later harvest the stored **result** — a detached run's stdout is
    otherwise lost.

    `agent_name` defaults to `agy-review`; `/agy:adversarial-review` reuses
    this function with `agent_name="agy-adversarial-review"`. The job
    `kind` is always "review" regardless — both commands are the **review**
    **kind** of **job** (see glossary), not a new kind.

    Carries `--print-timeout _BACKGROUND_PRINT_TIMEOUT_ARG`: a background
    job is documented as having no timeout ceiling (the `--timeout` +
    `--background` rejection in `run()`/`adversarial_review.run()`), but
    agy's own `--print-timeout` defaults to `5m0s` regardless of what the
    plugin does — leaving it unset would silently reimpose that 5-minute
    ceiling. See `companion.launch`'s module docstring."""
    job_id = state.generate_job_id("review")
    log_file = state.resolve_job_log_file(repo_root, job_id)
    output_file = state.resolve_job_output_file(repo_root, job_id)

    # The agent workspace is the detached run's cwd, so it must outlive this
    # process — hence the state dir, not a temp dir (see state.py). Staged
    # before the spawn so a broken install fails here, loudly, instead of
    # producing a job that silently falls back to agy's default agent.
    try:
        workspace = agent_workspace.materialize(
            state.resolve_job_workspace(repo_root, job_id), agent_name
        )
    except AgentWorkspaceError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    cmd = _agy_command(
        prompt, log_file, agent_name=agent_name, add_dir=repo_root,
        print_timeout=_BACKGROUND_PRINT_TIMEOUT_ARG,
    )

    try:
        proc = spawn(cmd, str(workspace), stdout_path=str(output_file))
    except FileNotFoundError:
        print(
            "error: agy is not installed or not on PATH. Run `/agy:setup` to check.",
            file=sys.stderr,
        )
        return 1

    # Stub spawns in tests return None or a bare object; a real
    # Popen always has .pid. getattr with a default keeps both shapes safe.
    pid = getattr(proc, "pid", None)

    state.upsert_job(repo_root, {
        "id": job_id,
        "kind": "review",
        "status": "running",
        "log_file": str(log_file),
        "output_file": str(output_file),
        "pid": pid,
        "conversation": None,
        "session_id": os.environ.get(state.SESSION_ID_ENV),
    })
    print(
        "Launched background review job {} (log: {}). Check progress with "
        "/agy:status.".format(job_id, log_file)
    )
    return 0


# --- diff / manifest text (target selection and sizing live in companion.git) --


def _diff_text(cwd, target):
    """The raw diff text for the resolved **review target**, embedded
    directly in the prompt sent to agy (see _REVIEW_INSTRUCTIONS_TEMPLATE).
    Mirrors companion.git.target_size's mode handling; like sizing,
    untracked files are not represented here (no `git diff` output exists
    for them)."""
    mode = target["mode"]
    if mode == "staged":
        return _git_output(cwd, ["diff", "--cached"])
    if mode == "branch":
        return _git_output(cwd, ["diff", "{}...HEAD".format(target["base_ref"])])
    # working-tree: staged + unstaged changes to tracked files.
    return _git_output(cwd, ["diff", "--cached"]) + _git_output(cwd, ["diff"])


def _tracked_files(cwd):
    """Return the repo's tracked files (`git ls-files`) — the **manifest**
    `run()` hands to `assemble_prompt`. A diff shows what CHANGED; this
    shows what EXISTS, independent of the resolved review target's mode, so
    the bound agent (which has no file-access tools of its own — see
    docs/review-schema-verdict.md, Finding C) can tell a file that is
    genuinely absent from one that is merely outside the diff."""
    return _lines(_git_output(cwd, ["ls-files"]))


# --- prompt assembly -------------------------------------------------------


def _format_manifest(manifest):
    """Format `manifest` (an iterable of tracked file paths, or None) into
    the `{manifest_section}` block `assemble_prompt` splices into the
    template. Returns "" when `manifest` is None or empty, so the section is
    cleanly absent from the prompt rather than an empty header — this is
    what keeps every pre-manifest caller of `assemble_prompt` unchanged.

    Caps the listed paths at `_MANIFEST_MAX_FILES` (deterministic: the same
    prefix of `manifest`, in the order given, every call). Anything past the
    cap is dropped, but the drop is always stated in the rendered text with
    the exact count and the cap itself — never a silent truncation, so the
    agent cannot mistake "not shown" for "does not exist"."""
    if not manifest:
        return ""
    files = list(manifest)
    shown = files[:_MANIFEST_MAX_FILES]
    lines = [
        "\n",
        "Tracked files in this repository (context for existence checks "
        "only — this list is not part of the diff and is not itself "
        "material to review):\n",
    ]
    lines.extend("- {}\n".format(path) for path in shown)
    remaining = len(files) - len(shown)
    if remaining > 0:
        lines.append(
            "... truncated: {} more tracked file(s) not shown (cap: {}).\n".format(
                remaining, _MANIFEST_MAX_FILES
            )
        )
    return "".join(lines)


def assemble_prompt(
    target, size, template=_DEFAULT_PROMPT_TEMPLATE, review_instructions="", manifest=None
):
    """Build the exact prompt text sent to agy. `template` and
    `review_instructions` are both injectable — `review_instructions` is the
    seam `run()` fills with the diff-embedding instructions from
    _REVIEW_INSTRUCTIONS_TEMPLATE; the detailed review guidelines themselves
    come from the bound `agy-review` agent's system prompt, not from here.

    `manifest` is the same kind of seam: an iterable of tracked file paths
    (typically `_tracked_files(cwd)`'s result), defaulted to None so every
    existing caller keeps working unchanged. See `_format_manifest` for how
    it is rendered and truncated."""
    return template.format(
        label=target["label"],
        files=size["files"],
        insertions=size["insertions"],
        deletions=size["deletions"],
        review_instructions=review_instructions,
        manifest_section=_format_manifest(manifest),
    )


# --- rendering ---------------------------------------------------------


def _render_human(payload):
    target = payload["target"]
    size = payload["size"]
    lines = []
    if payload["nothing_to_review"]:
        lines.append("Nothing to review: {} is empty.".format(target["label"]))
    else:
        origin = "explicit" if target["explicit"] else "auto-selected"
        lines.append("Review target: {} ({})".format(target["label"], origin))
    lines.append("Files touched: {}".format(size["files"]))
    lines.append("Insertions: +{}".format(size["insertions"]))
    lines.append("Deletions: -{}".format(size["deletions"]))
    lines.append("")
    lines.append("--- Prompt that would be sent to agy ---")
    lines.append(payload["prompt"])
    return "\n".join(lines)
