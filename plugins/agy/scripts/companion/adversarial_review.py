"""companion.adversarial_review — `/agy:adversarial-review`: the same
**review target** selection, `agy` launch, **bind** check, and **tolerant
parse** that `companion.review` built for `/agy:review`, but bound to the
`agy-adversarial-review` agent (a skeptical, attack-surface-focused system
prompt — see `plugins/agy/agents/agy-adversarial-review/agent.md`) and
steerable with a free-text `focus` argument after the flags.

This module does not fork review.py. Target resolution (`resolve_target`,
`target_size`, `_diff_text`), prompt assembly (`assemble_prompt`), the
foreground/background launch (`_run_live_review`, `_run_background_review`),
and the **bind** check inside them are all imported and reused unchanged,
now parameterized by `agent_name`. The only things unique to this command
are: the `agy-adversarial-review` agent name, a `focus`-aware
review-instructions template, and the `focus` CLI argument itself.

Per the glossary/contract: this is still the **review** **kind** of **job**,
not a new kind — `_run_background_review` records job `kind: "review"` for
both commands.
"""
import json
import sys

from companion import repo as repo_arg
from companion.git import (
    GitTargetError,
    ensure_git_repository,
    resolve_target,
    target_size,
)
from companion.launch import _AGY_TIMEOUT_SECONDS, positive_int_timeout
from companion.review import (
    _diff_text,
    _render_human,
    _run_background_review,
    _run_live_review,
    _tracked_files,
    assemble_prompt,
)

HELP = (
    "Resolve a review target, size the change, and either preview the prompt "
    "(--dry-run) or run a live agy review that argues against the change."
)

# No "staged" scope here: upstream's adversarial-review deliberately does not
# support --scope staged/unstaged, and this command follows it. /agy:review
# keeps "staged" for itself.
_SCOPES = ("auto", "working-tree", "branch")

_AGY_AGENT_NAME = "agy-adversarial-review"

# Mirrors review.py's _REVIEW_INSTRUCTIONS_TEMPLATE (the diff is
# embedded directly in the prompt for the same --sandbox headless-print-mode
# reason — see that module's docstring), plus a `{focus_line}` seam that
# carries the user's free-text `focus` argument through to agy verbatim when
# supplied, and is simply empty when it is not.
_REVIEW_INSTRUCTIONS_TEMPLATE = (
    "Adversarially review the following diff, per your system instructions: "
    "actively try to disprove that it is safe to ship, not just check it for "
    "bugs. Output only the JSON described in your system instructions, "
    "nothing else.\n"
    "{focus_line}"
    "\n"
    "```diff\n{diff}\n```\n"
)


def add_arguments(parser):
    repo_arg.add_repo_argument(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and size the review target, and print the prompt that would be sent to "
        "agy, without invoking it. Without this flag, adversarial-review runs the live "
        "foreground path.",
    )
    parser.add_argument(
        "--scope",
        choices=list(_SCOPES),
        default="auto",
        help="Which review target to resolve: auto (default), working-tree, or branch.",
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
    parser.add_argument(
        "focus",
        nargs="*",
        default=[],
        help="Free-text focus to steer the adversarial review (e.g. 'auth and tenant "
        "isolation'), appended after any flags. Optional — weighted heavily by the agent "
        "when given, but every other material issue is still reported.",
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

    focus = " ".join(args.focus).strip()
    focus_line = "User focus (weight this heavily): {}\n".format(focus) if focus else ""
    review_instructions = _REVIEW_INSTRUCTIONS_TEMPLATE.format(diff=diff_text, focus_line=focus_line)
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
        return _run_background_review(repo_root, prompt, agent_name=_AGY_AGENT_NAME)
    timeout = args.timeout if args.timeout is not None else _AGY_TIMEOUT_SECONDS
    return _run_live_review(
        repo_root, prompt, args.json, agent_name=_AGY_AGENT_NAME, timeout=timeout
    )
