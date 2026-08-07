"""companion.probe — the free, read-only **slash-command probe**: `agy -p
"/<cmd>" --output-format json`, answered without starting an agent turn.

A **leaf module** (see glossary and `companion.launch`'s own docstring): it
imports no subcommand module, so `setup.py` — or anything else — can import
from it without risking a circular import.

Verified against a real agy 1.1.11 binary on 2026-08-07 (see the mission
notes this module was built from; the raw envelope is reproduced below).
`agy 1.1.11` added non-interactive answers, in print mode, for a small set
of READ-ONLY slash commands: `/usage`, `/credits`, `/model`, `/effort`,
`/skills`. Run through `--output-format json`, each answers immediately —
`conversation_id` empty, `num_turns: 0`, `usage` all-zero — and returns a
typed `command.data` payload instead of (or in addition to) prose:

    {"conversation_id":"","status":"SUCCESS","response":"...",
     "duration_seconds":0,"num_turns":0,
     "usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,
              "cache_read_tokens":0,"total_tokens":0},
     "command":{"name":"model","data":{"id":"...","label":"...",
                "effort":"...","is_default":false}}}

No conversation is created and no model is called. This is the same free-
probe family as `setup._bind_probe_command` (an invalid `--model` that
fails local validation before any conversation starts) and
`delegate.CONVERSATION_NOT_FOUND_RE`'s resume-fallback trace (AGENTS.md
"Settled findings" / "Never spend a live agy -p run just to check ..."):
proof obtained for zero quota, never inferred from a version number when it
can instead be measured directly.

--------------------------------------------------------------------------
THE ONE LEGITIMATE EXCEPTION TO --disable-slash-commands
--------------------------------------------------------------------------
Every other command vector this plugin builds — `delegate._agy_command`,
`review._agy_command`, `setup._bind_probe_command` — carries
`--disable-slash-commands` UNCONDITIONALLY, because every one of those
prompts is USER-SUPPLIED TEXT, and agy has expanded slash commands in print
mode since 1.1.9. Without the flag, a user's task or review prompt that
happens to start with "/" would be silently reinterpreted as a slash
command instead of sent as the literal text the user typed.

`_probe_command` below is the one place in this plugin that deliberately
OMITS `--disable-slash-commands`, and that is not a relaxation of the rule
above — it is a different rule, satisfying the same underlying invariant:

    THE INVARIANT: user-supplied text is never slash-expanded.

`_probe_command`'s prompt is never user text. It is one of five hardcoded,
plugin-authored constants (`_READ_ONLY_COMMANDS` below) — the caller cannot
reach this function with an arbitrary string; `_probe_command` raises
`ValueError` on anything outside that whitelist. Sending `--disable-slash-
commands` here would not protect a user's text (there is none) — it would
break the probe outright, because the whole point of this vector is to
trigger slash-command EXPANSION so agy answers the command. This was
verified empirically (fact 6 of the mission this module was built from):
`agy -p "/usage" --disable-slash-commands` does NOT error — it sends
`"/usage"` to the model as literal prompt text, spends real quota, creates
a real conversation, and returns a hallucinated answer ("It looks like
you're asking for usage information! The `/usage` command isn't a
recognized slash command..."). That failure mode is silent in the same
family as this plugin's other silent-fallback traps (AGENTS.md "Never
trust an agy run that silently fell back") — wrong answer, exit 0, no
diagnostic — which is exactly why this exception is written down this
loudly instead of left to be rediscovered by someone "fixing" it back.

If you are about to add `--disable-slash-commands` to `_probe_command`:
don't. Read the paragraph above again, then read AGENTS.md's "Never spend a
live agy -p run just to check ..." entries for the sibling probes that
already established this family of zero-quota techniques.
--------------------------------------------------------------------------

Tri-state discipline, same as `setup._version_supported` and
`agy_log._bind_check`: a probe returns "ok" (a definite, typed answer) or
"unknown" (agy could not be asked, or its answer could not be trusted) —
never a guessed value standing in for either. A non-zero exit, a timeout, a
process that could not even start, unparseable JSON, a JSON object with no
`command` key, or a `command.name` that does not match what was actually
requested (someone else's answer, or a future agy reshaping the envelope)
all collapse to "unknown". `usage`/`num_turns` being all-zero is evidence
FOR this docstring's claim that the probe is free — it is not asserted as a
runtime correctness gate: agy could start reporting nonzero usage on a
future version of this same free path and the probe would still be
correct to trust `command.data`.

--------------------------------------------------------------------------
`agy models` — a second, related free primitive
--------------------------------------------------------------------------
`_run_models` below wraps a DIFFERENT command: `agy models` (no `-p`, no
slash command at all — an ordinary subcommand, exactly like `agy agents`).
It exists for one reason: `setup._probe_model_forwarding` needs a model id
that is GUARANTEED to differ from whatever is currently selected, to
forward as a discriminating probe (see that function's docstring for why a
probe that forwards the SAME value it just read back proves nothing).
Verified live against agy 1.1.11 on 2026-08-07: `agy models` exits 0 and
prints a `Fetching available models...` header line followed by
tab-separated `id\\tlabel` lines, one per available model on THIS account —
so a caller can pick any id that isn't the baseline without ever
hardcoding a slug that might not exist for a given user.
"""
import json
import subprocess

# Every command this module will ever build a probe for. `_probe_command`
# enforces this as a hard whitelist (raises ValueError otherwise) so the
# "prompt is always a plugin-authored constant, never user text" invariant
# holds structurally, not just by convention.
_READ_ONLY_COMMANDS = ("usage", "credits", "model", "effort", "skills")

# These commands answer immediately (duration_seconds: 0 in every capture
# used to build this module) — this is a generous ceiling for a slow
# machine or a cold agy start, not a tuned timeout for a real model turn.
_PROBE_TIMEOUT_SECONDS = 20

# `agy models` is a plain subcommand list, not a model call either — same
# ceiling rationale as _PROBE_TIMEOUT_SECONDS.
_MODELS_TIMEOUT_SECONDS = 20

_MODELS_HEADER = "Fetching available models..."


def _probe_command(agy_path, command, model=None, effort=None):
    """Build the probe's argv. Pure — no I/O — so it is trivially assertable
    directly in a test, the same discipline `delegate._agy_command` and
    `setup._bind_probe_command` already follow.

    `model`/`effort`, when given, are forwarded verbatim as `--model`/
    `--effort` — this is what lets a caller prove the round-trip: ask agy
    what it would use with them set, and compare the answer against what
    was asked for (see `setup._probe_model_forwarding`). Neither is
    required; a bare probe (no flags) reads whatever is currently
    selected.

    See the module docstring for why `--disable-slash-commands` is
    deliberately, permanently absent from this vector."""
    if command not in _READ_ONLY_COMMANDS:
        raise ValueError(
            "{!r} is not one of the whitelisted read-only probe commands "
            "{}".format(command, _READ_ONLY_COMMANDS)
        )
    cmd = [agy_path, "-p", "/" + command, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _run_probe(agy_path, command, model=None, effort=None, timeout=_PROBE_TIMEOUT_SECONDS):
    """Run `_probe_command(...)` and return a tri-state result:

        {"state": "ok", "command": command, "data": {...}, "detail": None}
        {"state": "unknown", "command": command, "data": None, "detail": "..."}

    "ok" is returned ONLY when every one of the following holds: the
    process started and exited within `timeout`; it exited 0; stdout
    parsed as a JSON object; that object carried a `"command"` object; that
    object's `"name"` matches the `command` that was actually requested
    (never trusted blindly — a mismatched name means either a different
    answer altogether or an envelope shape this function does not
    understand, and either way "ok" would be a guess); and a `"data"` key
    was present (even if its value is an empty object — `/skills` on an
    account with none registered is a legitimate empty answer, not a
    failure).

    Every other outcome — including a non-zero exit, a timeout, agy not
    being on PATH, unparseable JSON, a missing or malformed `"command"`
    object, or a name mismatch — is "unknown". Never conflated with a
    positive answer; never conflated with a definite negative either
    (there is no negative state here — a read-only probe either answers or
    it does not)."""
    cmd = _probe_command(agy_path, command, model=model, effort=effort)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "unknown", "command": command, "data": None, "detail": str(exc)}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = "agy -p \"/{}\" --output-format json exited {}{}".format(
            command, result.returncode, " ({})".format(stderr) if stderr else ""
        )
        return {"state": "unknown", "command": command, "data": None, "detail": detail}

    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        return {
            "state": "unknown",
            "command": command,
            "data": None,
            "detail": "could not parse JSON from agy stdout: {}".format(exc),
        }

    if not isinstance(payload, dict):
        return {
            "state": "unknown",
            "command": command,
            "data": None,
            "detail": "agy stdout parsed as JSON but was not an object",
        }

    envelope_command = payload.get("command")
    if not isinstance(envelope_command, dict):
        return {
            "state": "unknown",
            "command": command,
            "data": None,
            "detail": "agy JSON envelope carried no \"command\" object",
        }

    if envelope_command.get("name") != command:
        return {
            "state": "unknown",
            "command": command,
            "data": None,
            "detail": "requested /{} but the envelope's command.name was {!r}".format(
                command, envelope_command.get("name")
            ),
        }

    if "data" not in envelope_command:
        return {
            "state": "unknown",
            "command": command,
            "data": None,
            "detail": "envelope command carried no \"data\" key",
        }

    return {"state": "ok", "command": command, "data": envelope_command["data"], "detail": None}


def _models_command(agy_path):
    """Build `agy models`'s argv. Pure — no I/O — same discipline as
    `_probe_command`. Nothing to whitelist here: unlike `_probe_command`,
    this is not a `-p` prompt at all, so there is no slash-expansion
    invariant to protect and no `--disable-slash-commands` question."""
    return [agy_path, "models"]


def _parse_models(stdout):
    """Parse `agy models`' stdout into an ordered list of `(id, label)`
    tuples. Skips the `"Fetching available models..."` header line and any
    blank line; a line with no tab (malformed, or unexpected banner text
    from a future agy) is skipped rather than raising — this is agy's own
    freeform CLI output, not a JSON contract, so tolerance here matters as
    much as it does for `_normalize_usage_groups` in setup.py. Order is
    preserved and duplicates are left in (a caller that needs distinct ids
    dedupes itself, as `setup._probe_model_forwarding` does)."""
    models = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line == _MODELS_HEADER:
            continue
        if "\t" not in line:
            continue
        model_id, _sep, label = line.partition("\t")
        model_id = model_id.strip()
        if not model_id:
            continue
        models.append((model_id, label.strip()))
    return models


def _run_models(agy_path, timeout=_MODELS_TIMEOUT_SECONDS):
    """Run `agy models` and return a tri-state result:

        {"state": "ok", "models": [(id, label), ...], "detail": None}
        {"state": "unknown", "models": None, "detail": "..."}

    "ok" only when the process exits 0 within `timeout` and
    `_parse_models` recovers at least one `(id, label)` pair — a non-zero
    exit, a timeout, agy not being on PATH, or stdout that yields zero
    parseable lines are all "unknown". This function does not itself
    decide whether enough DISTINCT ids came back to pick a discriminating
    probe target; that judgment belongs to the caller
    (`setup._probe_model_forwarding`), which needs it to also account for
    the specific baseline id it already knows."""
    cmd = _models_command(agy_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "unknown", "models": None, "detail": str(exc)}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = "agy models exited {}{}".format(
            result.returncode, " ({})".format(stderr) if stderr else ""
        )
        return {"state": "unknown", "models": None, "detail": detail}

    models = _parse_models(result.stdout)
    if not models:
        return {
            "state": "unknown",
            "models": None,
            "detail": "agy models returned no parseable \"id<TAB>label\" lines",
        }
    return {"state": "ok", "models": models, "detail": None}
