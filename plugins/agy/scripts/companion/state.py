"""companion.state — the per-repository **state dir** under
$CLAUDE_PLUGIN_DATA, tracking agy **job** records.

Ported (logic only, not copied JS) from upstream's `scripts/lib/state.mjs`:
the **state dir** is keyed by a filesystem-safe slug of the repo directory
name plus a 16-hex-char sha256 of the canonicalized repo root, rooted under
`$CLAUDE_PLUGIN_DATA/state` (or a deterministic temp-dir fallback when that
env var is unset). It holds `state.json` (the job list) and a `jobs/`
directory (each job's persistent `--log-file` lives there — see
`resolve_job_log_file`). **Job**s are capped at 50, pruned oldest-by
`updated_at` first, same as upstream.

Pure state-dir/JSON I/O only — no subprocess, no agy, no log parsing (that
is `companion.status`'s job).
"""
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATE_VERSION = 1
PLUGIN_DATA_ENV = "CLAUDE_PLUGIN_DATA"
# Mirrors upstream's session-scoping env var
# (CODEX_COMPANION_SESSION_ID) with a product-prefixed name of our own.
# Claude Code sets this in the shell it runs plugin commands from so a
# **job** created by /agy:review --background can be tagged with the
# **session** that launched it, and /agy:status can later filter to just
# that session. Lives here (not in review.py or status.py) so both can
# import the same constant without a circular import between them.
SESSION_ID_ENV = "AGY_COMPANION_SESSION_ID"
# Mirrors upstream's `path.join(os.tmpdir(), "codex-companion")` —
# same shape, agy-companion name — a deterministic root so two calls in the
# same process (or two separate companion invocations) agree on the state
# dir even when $CLAUDE_PLUGIN_DATA is not set (e.g. a bare `python3
# agy_companion.py` outside the plugin harness). Ceiling: os.tmpdir() itself
# can differ across machines/users; that is upstream's ceiling too, and
# $CLAUDE_PLUGIN_DATA is always set when Claude Code actually runs the
# plugin, so this path is a dev/test fallback, not the production path.
_FALLBACK_STATE_ROOT = Path(tempfile.gettempdir()) / "agy-companion"
STATE_FILE_NAME = "state.json"
JOBS_DIR_NAME = "jobs"
MAX_JOBS = 50

_SLUG_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def resolve_state_dir(repo_root):
    """The state dir for `repo_root`: `<state root>/<slug>-<hash>`, where
    `<state root>` is `$CLAUDE_PLUGIN_DATA/state` (or the temp-dir fallback),
    `<slug>` is the repo directory's basename sanitized to
    `[A-Za-z0-9._-]`, and `<hash>` is sha256(realpath(repo_root))[:16]."""
    repo_root_path = Path(repo_root)
    try:
        canonical = str(repo_root_path.resolve())
    except OSError:
        canonical = str(repo_root_path)

    slug_source = repo_root_path.name or "repo"
    slug = _SLUG_UNSAFE_RE.sub("-", slug_source).strip("-") or "repo"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    plugin_data_dir = os.environ.get(PLUGIN_DATA_ENV)
    state_root = Path(plugin_data_dir) / "state" if plugin_data_dir else _FALLBACK_STATE_ROOT
    return state_root / "{}-{}".format(slug, digest)


def resolve_state_file(repo_root):
    return resolve_state_dir(repo_root) / STATE_FILE_NAME


def resolve_jobs_dir(repo_root):
    return resolve_state_dir(repo_root) / JOBS_DIR_NAME


def ensure_state_dir(repo_root):
    resolve_jobs_dir(repo_root).mkdir(parents=True, exist_ok=True)


def resolve_job_log_file(repo_root, job_id):
    """Where a **job**'s persistent `--log-file` lives. Called before the
    job is spawned, so it also ensures the state dir exists."""
    ensure_state_dir(repo_root)
    return resolve_jobs_dir(repo_root) / "{}.log".format(job_id)


def resolve_job_workspace(repo_root, job_id):
    """Where a **job**'s **agent workspace** lives — the directory a detached
    run uses as its cwd so `agy` can resolve the vendored agent from
    `.agents/agents/<name>/agent.md` (see companion.agent_workspace).

    Under the state dir rather than a temp dir on purpose: a background job
    outlives the process that launched it, and a TemporaryDirectory would be
    deleted the moment that process returns — while the job is still running.
    Same ensure/parent-dir behavior as the log and output files, called
    before the job is spawned."""
    ensure_state_dir(repo_root)
    return resolve_jobs_dir(repo_root) / "{}.workspace".format(job_id)


def resolve_job_output_file(repo_root, job_id):
    """Where a **job**'s persistent stdout capture lives — the detached
    agy's stdout (the **result**) would otherwise go to DEVNULL and be lost
    for good, since there is no babysitter process to hold onto it. Same
    ensure/parent-dir behavior as resolve_job_log_file, called before the
    job is spawned."""
    ensure_state_dir(repo_root)
    return resolve_jobs_dir(repo_root) / "{}.out".format(job_id)


def generate_job_id(prefix="job"):
    return "{}-{}".format(prefix, uuid.uuid4().hex[:12])


# --- state.json round trip --------------------------------------------------


def _default_state():
    return {"version": STATE_VERSION, "jobs": []}


def load_state(repo_root):
    state_file = resolve_state_file(repo_root)
    if not state_file.exists():
        return _default_state()
    try:
        parsed = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
    return {"version": STATE_VERSION, "jobs": jobs if isinstance(jobs, list) else []}


def _prune_jobs(jobs):
    ordered = sorted(jobs, key=lambda job: str(job.get("updated_at") or ""), reverse=True)
    return ordered[:MAX_JOBS]


def save_state(repo_root, state):
    ensure_state_dir(repo_root)
    next_jobs = _prune_jobs(state.get("jobs", []))
    next_state = {"version": STATE_VERSION, "jobs": next_jobs}
    resolve_state_file(repo_root).write_text(
        json.dumps(next_state, indent=2) + "\n", encoding="utf-8"
    )
    return next_state


def update_state(repo_root, mutate):
    """Load, apply `mutate(state)` in place, then save+prune. The
    load/mutate/save round trip through disk on every call — no in-memory
    cache — so a later turn's process sees an earlier turn's writes."""
    state = load_state(repo_root)
    mutate(state)
    return save_state(repo_root, state)


def upsert_job(repo_root, job_patch):
    """Insert a new **job** record or merge `job_patch` into the existing
    one matched by `job_patch["id"]`. New jobs get `created_at`/`updated_at`
    set to now (overridable by the patch, mirroring upstream); existing
    jobs always get `updated_at` bumped to now, regardless of the patch."""

    def mutate(state):
        timestamp = now_iso()
        jobs = state["jobs"]
        for i, job in enumerate(jobs):
            if job.get("id") == job_patch.get("id"):
                jobs[i] = dict(job, **job_patch)
                jobs[i]["updated_at"] = timestamp
                return
        new_job = {"created_at": timestamp, "updated_at": timestamp}
        new_job.update(job_patch)
        jobs.insert(0, new_job)

    return update_state(repo_root, mutate)


def list_jobs(repo_root):
    return load_state(repo_root)["jobs"]


def match_job(jobs, job_id):
    """Match `job_id` among `jobs` by exact id or unambiguous prefix,
    mirroring upstream's matchJobReference. Returns (job, error_message)."""
    exact = [job for job in jobs if job.get("id") == job_id]
    if exact:
        return exact[0], None
    prefix_matches = [job for job in jobs if str(job.get("id") or "").startswith(job_id)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], None
    if len(prefix_matches) > 1:
        return None, 'Job reference "{}" is ambiguous. Use a longer job id.'.format(job_id)
    return None, 'No job found for "{}". Run /agy:status to list known jobs.'.format(job_id)


def read_file_safe(path):
    """Safely read text from a path, returning '' if missing or empty."""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")
