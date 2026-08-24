"""companion.git — git-backed **review target** selection and sizing.

Resolves a **review target** (working tree, staged changes, or a branch diff
against a base ref) and sizes it (files touched, insertions, deletions).
Ported from upstream's `scripts/lib/git.mjs` (read-only reference, not
copied verbatim) to Python 3 stdlib.

A **leaf module** (glossary term): every one of `companion.review`,
`companion.delegate`, `companion.status`, `companion.result`,
`companion.cancel`, and `companion.adversarial_review` imports target
selection from here, so this module must never import any of them, nor
`companion.cli` (which imports all seven subcommands at module level and
would make any such import circular) — stdlib only.
"""
import subprocess
from pathlib import Path


class GitTargetError(Exception):
    """Target selection could not proceed: not a git repo, git missing, an
    unresolvable ref, or a failing git invocation."""


# --- target selection --------------------------------------------------


def resolve_target(cwd, scope, base_ref):
    """Resolve the review target. Mirrors upstream's resolveReviewTarget,
    plus a `staged` mode upstream doesn't have."""
    ensure_git_repository(cwd)

    if base_ref:
        return {
            "mode": "branch",
            "label": "branch diff against {}".format(base_ref),
            "base_ref": base_ref,
            "explicit": True,
        }

    if scope == "working-tree":
        return {
            "mode": "working-tree",
            "label": "working tree diff",
            "base_ref": None,
            "explicit": True,
        }

    if scope == "staged":
        return {
            "mode": "staged",
            "label": "staged changes",
            "base_ref": None,
            "explicit": True,
        }

    if scope == "branch":
        detected = _detect_default_branch(cwd)
        return {
            "mode": "branch",
            "label": "branch diff against {}".format(detected),
            "base_ref": detected,
            "explicit": True,
        }

    # scope == "auto" — argparse's `choices` already rejects anything else.
    state = _working_tree_state(cwd)
    if state["is_dirty"]:
        return {
            "mode": "working-tree",
            "label": "working tree diff",
            "base_ref": None,
            "explicit": False,
        }

    detected = _detect_default_branch(cwd)
    return {
        "mode": "branch",
        "label": "branch diff against {}".format(detected),
        "base_ref": detected,
        "explicit": False,
    }


def ensure_git_repository(cwd):
    """The repo root containing `cwd`, or a GitTargetError naming the path
    that failed. `cwd` is not necessarily the process cwd (`--repo <path>`,
    companion.repo), so both messages name the path and point at the flag.
    The `is_dir` guard comes first deliberately: `_run_git` passes
    `cwd=` straight to `subprocess.run`, which raises `FileNotFoundError`
    for a nonexistent directory — indistinguishable there from a missing
    `git` binary, and reported as "git is not installed"."""
    path = Path(cwd)
    if not path.is_dir():
        raise GitTargetError(
            "no such directory: {} — pass --repo <path> to name the repository.".format(path)
        )
    result = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise GitTargetError(
            "not a git repository: {} — pass --repo <path> to name one.".format(path.resolve())
        )
    return result.stdout.strip()


def _detect_default_branch(cwd):
    symbolic = _run_git(cwd, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if symbolic.returncode == 0:
        prefix = "refs/remotes/origin/"
        remote_head = symbolic.stdout.strip()
        if remote_head.startswith(prefix):
            return remote_head[len(prefix):]

    # Fixed candidate list (main/master/trunk; local ref first, then
    # origin/<name>) — the same heuristic upstream ships with. Ceiling: it
    # misses an unconventional default branch name when there is no `origin`
    # remote to read a symbolic HEAD from. Resolving that needs network, and
    # this path is offline by design.
    for candidate in ("main", "master", "trunk"):
        local = _run_git(cwd, ["show-ref", "--verify", "--quiet", "refs/heads/" + candidate])
        if local.returncode == 0:
            return candidate
        remote = _run_git(cwd, ["show-ref", "--verify", "--quiet", "refs/remotes/origin/" + candidate])
        if remote.returncode == 0:
            return "origin/" + candidate

    raise GitTargetError(
        "Unable to detect the repository default branch. Pass --base <ref> or use --scope working-tree."
    )


def _working_tree_state(cwd):
    staged = _lines(_git_output(cwd, ["diff", "--cached", "--name-only"]))
    unstaged = _lines(_git_output(cwd, ["diff", "--name-only"]))
    untracked = _lines(_git_output(cwd, ["ls-files", "--others", "--exclude-standard"]))
    return {
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "is_dirty": bool(staged or unstaged or untracked),
    }


# --- sizing --------------------------------------------------------------


def target_size(cwd, target):
    mode = target["mode"]

    if mode == "staged":
        files, insertions, deletions = _numstat(cwd, ["--cached"])
        return {"files": len(set(files)), "insertions": insertions, "deletions": deletions}

    if mode == "branch":
        files, insertions, deletions = _numstat(cwd, ["{}...HEAD".format(target["base_ref"])])
        return {"files": len(set(files)), "insertions": insertions, "deletions": deletions}

    # working-tree
    state = _working_tree_state(cwd)
    staged_files, staged_ins, staged_del = _numstat(cwd, ["--cached"])
    unstaged_files, unstaged_ins, unstaged_del = _numstat(cwd, [])
    # Untracked files add to the file count only: they never appear in any
    # `git diff`, and this function sizes the change rather than reading file
    # content, so there is no line-accurate insertion count for them.
    all_files = set(staged_files) | set(unstaged_files) | set(state["untracked"])
    return {
        "files": len(all_files),
        "insertions": staged_ins + unstaged_ins,
        "deletions": staged_del + unstaged_del,
    }


def _numstat(cwd, diff_args):
    output = _git_output(cwd, ["diff", "--numstat"] + diff_args)
    files = []
    insertions = 0
    deletions = 0
    for line in _lines(output):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ins_field, del_field, path = parts[0], parts[1], parts[2]
        files.append(path)
        if ins_field.isdigit():
            insertions += int(ins_field)
        if del_field.isdigit():
            deletions += int(del_field)
    return files, insertions, deletions


# --- git plumbing ------------------------------------------------------


def _run_git(cwd, args):
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise GitTargetError("git is not installed. Install Git and retry.")
    except subprocess.TimeoutExpired:
        raise GitTargetError("git {} timed out".format(" ".join(args)))


def _git_output(cwd, args):
    result = _run_git(cwd, args)
    if result.returncode != 0:
        raise GitTargetError(
            "git {} failed: {}".format(" ".join(args), result.stderr.strip())
        )
    return result.stdout


def _lines(text):
    return [line for line in text.strip().split("\n") if line]
