"""companion.setup — the `setup` doctor: reports agy availability and
version, best-effort authentication state, registered agents, whether the
vendored `agy-review` custom agent actually **binds**, whether `--model`
forwarding is proven to take effect (`--effort` deliberately excluded from
that proof — see `_probe_model_forwarding`'s docstring), and a per-model
quota summary from `/usage` — the last two built on the free, zero-quota
read-only slash-command probes `companion.probe` adds on agy 1.1.11.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from companion import agent_workspace
from companion import probe
from companion.agent_workspace import AgentWorkspaceError
from companion.agy_log import _bind_check
from companion.launch import _print_timeout_arg

HELP = "Check whether agy is installed, authenticated, and which agents are registered."

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")

# One supported binary, no dual code path: an agy below this floor is not a
# degraded mode, it is unsupported. Report below-floor distinctly rather than
# silently printing the raw version string, so an unsupported binary fails at
# the doctor rather than somewhere less legible. The doctor's floor and the
# command vectors' assumptions must stay the same number.
#
# History of why this floor has moved, oldest first:
#
# 1.1.10 was the floor because below it `agy -p` ACCEPTS `--model` and
# `--effort` and then ignores them, silently running the persisted or default
# model instead (agy 1.1.10 release notes). `/agy:delegate` forwards both
# flags, so on an older binary it would quietly run a model the user did not
# ask for — a **silent fallback** with no trace to guard against, which is
# why that was a floor rather than a runtime check: on 1.1.10 the plugin had
# no way to OBSERVE whether --model/--effort actually took effect, so the
# version number itself was the only available proof, inferred rather than
# measured.
#
# 1.1.11 is the floor now, for exactly the reason 1.1.10's floor could not
# be a runtime check: 1.1.11 added non-interactive answers for read-only
# slash commands in print mode (`companion.probe`), which makes the
# `--model`/`--effort` forwarding question directly MEASURABLE — probe
# `/model` with them forwarded and compare the answer against what was
# asked for (`_probe_model_forwarding` below) — rather than assumed from a
# version string. Below 1.1.11, `/model` is not guaranteed to answer for
# free at all (per AGENTS.md's settled finding that 1.1.9 started expanding
# slash commands in print mode generally, well before 1.1.11 taught agy to
# answer some of them without spending a turn), so the doctor cannot run
# that probe safely on an older binary either. One supported binary, no
# dual code path: rather than carry a runtime check that only works above
# 1.1.11 and a version-inference fallback below it, the floor simply moves
# up to the version where the plugin can prove what it used to have to
# assume.
MIN_AGY_VERSION = (1, 1, 11)
MIN_AGY_VERSION_STR = "1.1.11"

# Agy 1.1.6 has no first-class auth-status API (verified — see
# AGENTS.md "Settled findings"). We classify auth state from the exit code
# and stderr text of the `agy agents` probe, matched against known
# not-authenticated stderr wording. Ceiling: brittle to agy changing that
# wording, and any stderr text we don't recognize collapses to "unknown"
# rather than a guess. Upgrade path: replace this heuristic wholesale if a
# future agy version ships a real `agy whoami`/auth-status command.
_NOT_AUTHENTICATED_MARKERS = (
    "No valid authentication found",
    "You are not logged into Antigravity",
)

_REVIEW_AGENT_NAME = "agy-review"
# Agy's local --model validation runs after agent resolution but
# before any model call is made (verified — see
# docs/review-schema-verdict.md, Finding A), which makes an intentionally
# invalid --model a free, zero-quota way to prove agy-review genuinely
# **binds** — the same technique review.py's own foreground launch relies
# on (_bind_check, FALLBACK_RE). `agy agents` cannot answer this: it only
# ever lists GLOBAL custom agents, and agy-review is workspace-scoped (see
# AGENTS.md's settled findings).
_BIND_PROBE_MODEL = "agy-setup-bind-probe-invalid-model"
_BIND_PROBE_PROMPT = "ping"
_BIND_PROBE_TIMEOUT_SECONDS = 30

# Both `_probe_model_forwarding` and `_probe_quota` below require the new
# 1.1.11 free read-only slash-command probes (companion.probe). Skip
# rather than run them on a below-floor binary: below 1.1.11 there is no
# guarantee `/model`/`/usage` answer without spending a turn (AGENTS.md's
# settled finding: 1.1.9 started expanding slash commands generally, well
# before 1.1.11 taught agy to answer some of them for free), and this
# doctor must never spend live agy quota just to populate a report.
_PROBE_UNSUPPORTED_DETAIL = (
    "requires agy >= {} for free, non-interactive slash-command answers "
    "(see MIN_AGY_VERSION's comment)".format(MIN_AGY_VERSION_STR)
)


def add_arguments(parser):
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON object instead of the human report.",
    )


def run(args):
    report = _doctor()
    if args.json:
        print(json.dumps(report))
    else:
        print(_render_human(report))
    return 0


def _doctor():
    agy_path = shutil.which("agy")
    if agy_path is None:
        detail = "agy is not installed"
        return {
            "agy": {
                "installed": False,
                "version": None,
                "min_version": MIN_AGY_VERSION_STR,
                "version_supported": None,
            },
            "auth": {"state": "unknown", "detail": detail},
            "agents": {"state": "error", "names": [], "detail": detail},
            "review_bind": {"state": "skipped", "detail": detail},
            "stream_json": {"state": "skipped", "detail": detail},
            "model_forwarding": {"state": "skipped", "detail": detail},
            "quota": {"state": "skipped", "groups": None, "detail": detail},
        }

    version = _probe_version(agy_path)
    supported = _version_supported(version)
    auth, agents = _probe_agents(agy_path)
    review_bind = _probe_review_bind(agy_path)
    return {
        "agy": {
            "installed": True,
            "version": version,
            "min_version": MIN_AGY_VERSION_STR,
            "version_supported": supported,
        },
        "auth": auth,
        "agents": agents,
        "review_bind": review_bind,
        "stream_json": _stream_json_state(version, supported),
        "model_forwarding": _probe_model_forwarding(agy_path, supported),
        "quota": _probe_quota(agy_path, supported),
    }


def _version_supported(version):
    """Compare a `\\d+\\.\\d+\\.\\d+` version string (already validated by
    `_VERSION_RE`) against `MIN_AGY_VERSION`. Returns True/False, or None
    when `version` is None (agy's `--version` output could not be parsed) —
    None must never be conflated with either boolean; it is its own
    "we don't know" state, same discipline as `_bind_check`'s "unknown"."""
    if not version:
        return None
    return tuple(int(part) for part in version.split(".")[:3]) >= MIN_AGY_VERSION


def _stream_json_state(version, supported):
    """Whether the installed agy supports `--output-format stream-json` —
    the signal callers need before relying on the **event stream** at all.

    Derived from `supported` (i.e. from `MIN_AGY_VERSION`) rather than a
    live probe of `--output-format stream-json` itself: this doctor check
    must not spend agy quota (AGENTS.md's "Never spend a live `agy -p` run
    just to check whether an agent bound" is the same principle). This is
    NOT the same claim as "MIN_AGY_VERSION is the stream-json floor" —
    1.1.8 is the first version known to support stream-json, and
    `MIN_AGY_VERSION` has since moved past it for unrelated reasons (see
    that constant's comment): first to 1.1.10 for `--model`/`--effort`
    forwarding, then to 1.1.11 because that forwarding claim became
    measurable instead of assumed. Every version `supported` accepts is
    therefore well above 1.1.8, so "supported" here still soundly implies
    real stream-json support — but "unsupported" is a wider bucket than
    "predates stream-json": it also catches 1.1.8/1.1.9/1.1.10 binaries
    that genuinely support stream-json but fail this plugin's OTHER
    version requirements. That is intentional (one supported binary, no
    dual code path) but means this state answers "is stream-json safe to
    rely on given everything else this plugin also needs", not literally
    "does this binary support stream-json". If some agy version ever
    regresses stream-json support independently of its version number,
    this needs a real capability probe (e.g. parsing `agy --help`) instead
    of a version comparison."""
    if supported is None:
        detail = (
            "agy --version did not return a parseable version string ({!r})".format(version)
        )
        return {"state": "unknown", "detail": detail}
    if supported:
        return {
            "state": "supported",
            "detail": "agy {} >= {} (this plugin's supported-version floor, "
            "itself above the 1.1.8 stream-json floor)".format(
                version, MIN_AGY_VERSION_STR
            ),
        }
    return {
        "state": "unsupported",
        "detail": "agy {} < {} — upgrade agy to use --output-format stream-json".format(
            version, MIN_AGY_VERSION_STR
        ),
    }


def _probe_version(agy_path):
    try:
        result = subprocess.run(
            [agy_path, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION_RE.search(result.stdout)
    return match.group(1) if match else None


def _probe_agents(agy_path):
    """One `agy agents` call serves double duty: its stdout is the
    registered-agents list, and its exit code / stderr are the only signal
    agy 1.1.6 gives us about auth state (see _NOT_AUTHENTICATED_MARKERS)."""
    try:
        result = subprocess.run(
            [agy_path, "agents"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail = str(exc)
        return (
            {"state": "unknown", "detail": detail},
            {"state": "error", "names": [], "detail": detail},
        )

    if result.returncode == 0:
        names = _parse_agent_names(result.stdout)
        return (
            {"state": "authenticated", "detail": None},
            {"state": "ok", "names": names, "detail": None},
        )

    stderr = result.stderr.strip()
    if any(marker in stderr for marker in _NOT_AUTHENTICATED_MARKERS):
        auth_state = "not_authenticated"
    else:
        auth_state = "unknown"
    detail = stderr or "agy agents exited {}".format(result.returncode)
    return (
        {"state": auth_state, "detail": detail},
        {"state": "error", "names": [], "detail": detail},
    )


def _bind_probe_command(agy_path, log_file):
    """Build the bind probe's argv. Pure — no I/O — so it is trivial to
    assert on directly (mirrors `review._agy_command`'s own pure-command
    test). Carries `--disable-slash-commands` unconditionally, same
    discipline applied to every other command vector in this plugin —
    see `_probe_review_bind`'s docstring for why fidelity to that vector
    matters here specifically.

    Also carries `--print-timeout`, derived from `_BIND_PROBE_TIMEOUT_SECONDS`
    the same way `review._launch_agy` derives one from its own subprocess
    timeout (`companion.launch._print_timeout_arg`). JUDGMENT CALL, written
    down rather than left silent: this probe always exits on LOCAL `--model`
    validation before any conversation is created (Finding A, this module's
    own docstring), so `--print-timeout` can never functionally matter here
    — the wait it bounds is never reached. It is added anyway because
    `_probe_review_bind`'s docstring states fidelity to the real
    `review._agy_command` vector is "the whole point" of this probe; now
    that the real vector always carries the flag, omitting it here would be
    exactly the kind of silent divergence that docstring warns against, for
    a flag that costs nothing to add. Fidelity wins over minimalism."""
    # --print-timeout follows --log-file, matching where review._agy_command
    # and delegate._agy_command splice the same flag into their own vectors
    # — one consistent position across every command-vector builder in this
    # plugin, not a per-function accident.
    return [
        agy_path, "-p", _BIND_PROBE_PROMPT,
        "--disable-slash-commands",
        "--agent", _REVIEW_AGENT_NAME,
        "--model", _BIND_PROBE_MODEL,
        "--sandbox",
        "--new-project",
        "--log-file", str(log_file),
        "--print-timeout", _print_timeout_arg(_BIND_PROBE_TIMEOUT_SECONDS),
    ]


def _probe_review_bind(agy_path):
    """The zero-quota **bind** probe for the vendored `agy-review` custom
    agent (`plugins/agy/agents/agy-review/agent.md`): `agy -p <trivial
    prompt> --disable-slash-commands --agent agy-review --model <invalid>
    --sandbox --new-project --log-file <path>`, reading the log for the
    fallback line vs. a `Created conversation` trace — never trusting stdout
    or the exit code (per Finding A). The `--log-file` is an ephemeral temp
    path, like review.py's own foreground probe — this is a one-off doctor
    check, not a persistent job.

    Fidelity to the vector `review._agy_command` actually builds is the whole
    point: any divergence answers a different question than "will /agy:review
    work?". So it runs in an **agent workspace** with cwd set to it exactly as
    `/agy:review` does (see companion.agent_workspace) rather than in whatever
    directory `/agy:setup` was invoked from, and it carries
    `--disable-slash-commands` unconditionally like every other vector.

    IMPORTANT — this probe can only ever DISPROVE a **bind**, never prove
    one: agy validates `--model` locally and exits before any conversation is
    created, so a `Created conversation` line — the only genuine positive
    **bind proof** `_bind_check` recognizes — is structurally unreachable on
    this path. A `"bound"` state can never come back from this function; the
    caller (`_render_human`) must not render one as if it could."""
    try:
        with tempfile.TemporaryDirectory(prefix="agy-setup-bind-") as tmp, \
                agent_workspace.ephemeral(_REVIEW_AGENT_NAME) as workspace:
            log_file = Path(tmp) / "agy.log"
            cmd = _bind_probe_command(agy_path, log_file)
            try:
                subprocess.run(
                    cmd,
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=_BIND_PROBE_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"state": "unknown", "detail": str(exc)}

            log_text = log_file.read_text(encoding="utf-8", errors="ignore") if log_file.exists() else ""
            state, proof = _bind_check(log_text)
            return {"state": state, "detail": proof}
    except AgentWorkspaceError as exc:
        # A missing vendored agent is a definite "will not bind", not an
        # "unknown" — report it as the actionable failure it is.
        return {"state": "not_bound", "detail": str(exc)}


def _probe_model_forwarding(agy_path, supported):
    """Prove — never assume — that `--model` forwarded to `agy -p` actually
    takes effect, using the free 1.1.11 `/model` probe (`companion.probe`).
    This is the runtime check `MIN_AGY_VERSION`'s comment says 1.1.10's
    floor could not be: below 1.1.11 the plugin could only infer the flag
    worked from the version number; on 1.1.11 it can read agy's own answer
    back.

    THE DEFECT AN EARLIER VERSION OF THIS FUNCTION HAD, and why the design
    below looks the way it does: the first version of this check read the
    currently-selected model from a bare `/model` probe, then forwarded
    THAT SAME VALUE back and asserted it echoed. That is a tautology. If
    `--model` is silently IGNORED — the exact bug class this check exists
    to catch — agy falls back to the persisted/default model, which is the
    baseline value that was just read. Honored and ignored produce
    byte-identical output; the broken check reported "confirmed" on a
    binary where forwarding was completely broken. A probe only proves
    anything if it forwards a value that DIFFERS from what agy would pick
    on its own, and then checks the answer actually changed to match.

    Design choice — what differing model to probe WITH: this function
    never hardcodes a model slug (a hardcoded slug could be invalid on a
    given account — the same problem `_BIND_PROBE_MODEL` sidesteps by
    being deliberately INVALID rather than a real model name — and a probe
    that fails because the slug does not exist would be indistinguishable
    from a probe that fails because forwarding is broken). Instead: read
    the CURRENTLY-SELECTED model from a bare `/model` probe (the
    baseline), list every model actually available on this account via
    the free `agy models` (`probe._run_models`), and pick any listed id
    that is NOT the baseline. Forward that id with `--model` and assert
    `/model`'s answer comes back as exactly that id — a change agy could
    only produce by genuinely honoring the flag, never by falling back to
    what it already had selected.

    This probe does NOT persist the selection: a bare `/model` probe run
    immediately afterward reads back the original baseline again
    (verified live against agy 1.1.11 on 2026-08-07 — `--model` is
    session-scoped to the one `-p` invocation it is passed to). This is
    what makes it safe to run unconditionally from a doctor: it never
    mutates the user's actually-configured model.

    Tri-state: "confirmed" (the forwarded, differing model came back
    exactly), "mismatch" (agy answered with some other model than what was
    forwarded — a loud, actionable failure, the silent-fallback bug class
    the version floor exists to guard against), "unknown" (the `/model`
    probe or `agy models` could not be trusted, OR `agy models` listed
    fewer than 2 distinct ids — nothing differs from the baseline to
    forward, so there is nothing to discriminate with; this must never be
    reported as "confirmed" by default), or "skipped" (below-floor binary;
    see `_PROBE_UNSUPPORTED_DETAIL`).

    Why `--effort` is not part of this proof: an earlier draft forwarded
    `--effort` alongside `--model` and asserted both round-tripped. But
    several models `agy models` lists carry no effort variant at all
    (`claude-sonnet-4-6`, `gpt-oss-120b-medium` are real examples), so an
    effort-based assertion is not universally available the way a
    differing model id is — every candidate this function picks is
    guaranteed to differ from the baseline by id alone. `_probe_command`
    still accepts and forwards `--effort` (exercised directly in
    tests/test_probe.py); this function simply does not build its proof on
    it."""
    if not supported:
        return {"state": "skipped", "detail": _PROBE_UNSUPPORTED_DETAIL}

    baseline = probe._run_probe(agy_path, "model")
    if baseline["state"] != "ok":
        return {
            "state": "unknown",
            "detail": "could not read the currently-selected model to probe with "
            "({})".format(baseline["detail"]),
        }

    baseline_data = baseline["data"] or {}
    baseline_model = baseline_data.get("id")
    if not baseline_model:
        return {
            "state": "unknown",
            "detail": "the /model probe's answer carried no usable model id",
        }

    models_result = probe._run_models(agy_path)
    if models_result["state"] != "ok":
        return {
            "state": "unknown",
            "detail": "could not list available models via `agy models` to choose "
            "a differing probe target ({})".format(models_result["detail"]),
        }

    distinct_ids = []
    for model_id, _label in models_result["models"]:
        if model_id not in distinct_ids:
            distinct_ids.append(model_id)
    if len(distinct_ids) < 2:
        return {
            "state": "unknown",
            "detail": "agy models listed fewer than 2 distinct models ({}); nothing "
            "differs from the baseline {} to forward as a discriminating "
            "probe".format(distinct_ids, baseline_model),
        }

    candidate_model = next((m for m in distinct_ids if m != baseline_model), None)
    if candidate_model is None:
        # Structurally unreachable given the >= 2 distinct check above (if
        # every distinct id equalled baseline_model there could not be 2 of
        # them) — kept as an explicit fail-closed guard rather than trusting
        # that invariant silently.
        return {
            "state": "unknown",
            "detail": "could not find a model differing from the baseline {} in "
            "agy models' output".format(baseline_model),
        }

    forwarded = probe._run_probe(agy_path, "model", model=candidate_model)
    if forwarded["state"] != "ok":
        return {
            "state": "unknown",
            "detail": "forwarding --model {} did not get an answer back "
            "({})".format(candidate_model, forwarded["detail"]),
        }

    forwarded_data = forwarded["data"] or {}
    effective_model = forwarded_data.get("id")

    result = {
        "baseline_model": baseline_model,
        "requested_model": candidate_model,
        "effective_model": effective_model,
    }
    if effective_model != candidate_model:
        result["state"] = "mismatch"
        result["detail"] = (
            "baseline model was {}; forwarded --model {} (deliberately "
            "different), but agy's own /model answer came back {} instead "
            "— --model forwarding is NOT taking effect on this "
            "binary.".format(baseline_model, candidate_model, effective_model)
        )
        return result
    result["state"] = "confirmed"
    result["detail"] = (
        "baseline model was {}; forwarded a deliberately different --model {}, "
        "and agy's own /model answer changed to exactly that — forwarding is "
        "genuinely taking effect.".format(baseline_model, candidate_model)
    )
    return result


# `/credits` is NOT the quota signal — do not "fix" this doctor by reading
# it instead. Verified live: on a subscription account `/credits` reports
# `Remaining credits 0` while `/usage` reports 100% weekly remaining in the
# same moment — they are different axes (a credits BALANCE vs. a rate-limit
# WINDOW), and agy 1.1.11's own release notes list a fix for a bug where an
# empty credits response was previously read as a balance of zero. `/usage`
# is the only reliable quota signal this doctor uses.
def _probe_quota(agy_path, supported):
    """Free 1.1.11 `/usage` probe: per-model-group rate-limit buckets
    (weekly and 5-hour windows), each with a remaining fraction and a reset
    time. Tri-state: "ok" (a usable, if possibly empty, `groups` list),
    "unknown" (the probe did not answer, or answered with a shape this
    function cannot make sense of), or "skipped" (below-floor binary)."""
    if not supported:
        return {"state": "skipped", "groups": None, "detail": _PROBE_UNSUPPORTED_DETAIL}

    result = probe._run_probe(agy_path, "usage")
    if result["state"] != "ok":
        return {"state": "unknown", "groups": None, "detail": result["detail"]}

    groups = _normalize_usage_groups((result["data"] or {}).get("groups"))
    if groups is None:
        return {
            "state": "unknown",
            "groups": None,
            "detail": "the /usage answer carried no usable \"groups\" list",
        }
    return {"state": "ok", "groups": groups, "detail": None}


def _normalize_usage_groups(raw_groups):
    """Tolerant parse of `/usage`'s `command.data.groups`: never crashes on
    a missing or malformed field, since this is agy's own JSON, not
    something this plugin controls the shape of. Returns `None` (not an
    empty list) when `raw_groups` itself is missing or not a list — that is
    "cannot make sense of this answer", a distinct state from "answered
    with zero groups". Every per-bucket field is read with `.get`, so a
    bucket missing the OPTIONAL `description` key (verified: present on
    some buckets, absent on others, in the same real `/usage` answer) reads
    as `None` rather than raising, and a non-dict entry anywhere in the
    list is skipped rather than crashing the whole probe."""
    if not isinstance(raw_groups, list):
        return None
    groups = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        raw_buckets = raw_group.get("buckets")
        buckets = []
        if isinstance(raw_buckets, list):
            for raw_bucket in raw_buckets:
                if not isinstance(raw_bucket, dict):
                    continue
                buckets.append({
                    "id": raw_bucket.get("id"),
                    "name": raw_bucket.get("name"),
                    "window": raw_bucket.get("window"),
                    "remaining_fraction": raw_bucket.get("remaining_fraction"),
                    "reset_time": raw_bucket.get("reset_time"),
                    "description": raw_bucket.get("description"),
                })
        groups.append({
            "name": raw_group.get("name"),
            "description": raw_group.get("description"),
            "buckets": buckets,
        })
    return groups


def _parse_agent_names(stdout):
    """`agy agents` prints "Available agents:" followed by two-space-indented
    custom agent names, one per line (verified — see AGENTS.md "Settled
    findings"). An empty list after the header is a legitimate state."""
    lines = stdout.splitlines()
    names = []
    in_list = False
    for line in lines:
        if line.strip() == "Available agents:":
            in_list = True
            continue
        if not in_list:
            continue
        if line.startswith("  ") and line.strip():
            names.append(line.strip())
        elif line.strip():
            break
    return names


def _render_human(report):
    lines = []
    agy = report["agy"]
    if agy["installed"]:
        version_str = agy["version"] or "unknown"
        if agy["version_supported"] is False:
            lines.append(
                "agy: installed (version {}) — BELOW MINIMUM SUPPORTED VERSION {}. "
                "Upgrade agy before relying on this plugin.".format(
                    version_str, agy["min_version"]
                )
            )
        elif agy["version_supported"] is None:
            lines.append(
                "agy: installed (version {}) — could not verify against the "
                "minimum supported version {}.".format(version_str, agy["min_version"])
            )
        else:
            lines.append("agy: installed (version {})".format(version_str))
    else:
        lines.append("agy: not installed")

    auth = report["auth"]
    suffix = " ({})".format(auth["detail"]) if auth["detail"] else ""
    lines.append("auth: {}{}".format(auth["state"], suffix))

    agents = report["agents"]
    if agents["state"] == "ok":
        if agents["names"]:
            lines.append("agents: " + ", ".join(agents["names"]))
        else:
            lines.append("agents: none registered")
    else:
        suffix = " ({})".format(agents["detail"]) if agents["detail"] else ""
        lines.append("agents: could not list{}".format(suffix))

    review_bind = report["review_bind"]
    if review_bind["state"] in ("bound", "unknown"):
        # The zero-quota probe (`_probe_review_bind`) can only ever DISPROVE
        # a **bind**, never prove one: it exits on local `--model`
        # validation before any conversation is created, so a `Created
        # conversation` line — the only genuine positive **bind proof** —
        # is structurally unreachable here. "bound" and "unknown" therefore
        # render identically: no **agent fallback** observed is real
        # information (the negative signal works and costs nothing), but it
        # is not a bind confirmation, so this must not read as a green
        # "will work" promise. `/agy:review` verifies the bind on its own
        # log at run time and fails closed if it cannot — that is where a
        # bind is actually proven.
        lines.append(
            "agy-review agent: no agent fallback detected (proof: {}) — this "
            "is UNKNOWN, not a confirmed bind. This zero-quota probe can only "
            "disprove a bind, never prove one; it exits before any "
            "conversation is created. /agy:review verifies the bind for real "
            "on its own log at run time, and fails closed if it "
            "cannot.".format(review_bind["detail"])
        )
    elif review_bind["state"] == "not_bound":
        lines.append(
            "agy-review agent: NOT BOUND — /agy:review will NOT work "
            "(proof: {}). Check that plugins/agy/agents/agy-review/agent.md "
            "is present in this repo, and check your agy version — the "
            "bind requires --new-project support (verified against agy "
            "{}+; see docs/review-schema-verdict.md).".format(
                review_bind["detail"], MIN_AGY_VERSION_STR
            )
        )
    elif review_bind["state"] == "skipped":
        lines.append("agy-review agent: skipped ({})".format(review_bind["detail"]))
    else:
        lines.append(
            "agy-review agent: could not check ({})".format(review_bind["detail"])
        )

    stream_json = report["stream_json"]
    if stream_json["state"] == "supported":
        lines.append("stream-json: supported ({})".format(stream_json["detail"]))
    elif stream_json["state"] == "unsupported":
        lines.append(
            "stream-json: NOT SUPPORTED ({}). Features that depend on "
            "--output-format stream-json will not work.".format(stream_json["detail"])
        )
    elif stream_json["state"] == "skipped":
        lines.append("stream-json: skipped ({})".format(stream_json["detail"]))
    else:
        lines.append(
            "stream-json: could not determine ({})".format(stream_json["detail"])
        )

    model_forwarding = report["model_forwarding"]
    if model_forwarding["state"] == "confirmed":
        lines.append(
            "--model forwarding: confirmed ({})".format(model_forwarding["detail"])
        )
    elif model_forwarding["state"] == "mismatch":
        lines.append(
            "--model forwarding: MISMATCH — {} Baseline was {}, forwarded {}, agy used "
            "{} instead. This is the exact silent-fallback bug class MIN_AGY_VERSION "
            "exists to guard against — do not trust --model/--effort forwarding on "
            "this binary until this is resolved.".format(
                model_forwarding["detail"],
                model_forwarding["baseline_model"],
                model_forwarding["requested_model"],
                model_forwarding["effective_model"],
            )
        )
    elif model_forwarding["state"] == "skipped":
        lines.append(
            "--model forwarding: skipped ({})".format(model_forwarding["detail"])
        )
    else:
        lines.append(
            "--model forwarding: could not determine ({})".format(
                model_forwarding["detail"]
            )
        )

    quota = report["quota"]
    if quota["state"] == "ok":
        if quota["groups"]:
            lines.append("quota (/usage):")
            for group in quota["groups"]:
                lines.append("  {}:".format(group["name"] or "(unnamed group)"))
                if not group["buckets"]:
                    lines.append("    (no buckets reported)")
                for bucket in group["buckets"]:
                    lines.append("    {}".format(_render_usage_bucket(bucket)))
        else:
            lines.append("quota (/usage): no groups reported")
    elif quota["state"] == "skipped":
        lines.append("quota (/usage): skipped ({})".format(quota["detail"]))
    else:
        lines.append("quota (/usage): could not determine ({})".format(quota["detail"]))

    return "\n".join(lines)


def _render_usage_bucket(bucket):
    """One `/usage` bucket, human-legible: name (or id as a fallback),
    remaining fraction as a percentage (or "unknown" if agy did not send a
    number), reset time, and the OPTIONAL description when present."""
    label = bucket["name"] or bucket["id"] or "(unnamed bucket)"
    fraction = bucket["remaining_fraction"]
    if isinstance(fraction, (int, float)):
        percent = "{:.1f}% remaining".format(fraction * 100)
    else:
        percent = "remaining fraction unknown"
    reset = bucket["reset_time"] or "reset time unknown"
    line = "{}: {} (resets {})".format(label, percent, reset)
    if bucket["description"]:
        line += " — {}".format(bucket["description"])
    return line
