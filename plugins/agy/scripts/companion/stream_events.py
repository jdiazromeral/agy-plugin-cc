"""companion.stream_events — parses `agy`'s structured **event stream**
(`--output-format stream-json`) into a typed record.

The **event stream** is NDJSON: one JSON object per line, an **init event**
first, then N **step update**s as the run proceeds, then exactly one
**result event** carrying `conversation_id`, `status`, `response`, and
`usage` (glossary). This module reads that stream and produces an
`EventStream` record exposing those fields plus the ordered **step update**
sequence.

This sits *beside* the `--log-file` reader (`companion/agy_log.py`), not in
place of it: the stream carries no agent field, so the **bind** check keeps
reading the log regardless.

Parsed against real, committed bytes only — never hand-written NDJSON — per
`tests/fixtures/stream_events/PROVENANCE.md` and the discipline
`AGENTS.md` already states for `tests/fake_agy.py`.

`response`'s content is never assumed to be well-formed JSON. This plugin
does not use `--json-schema` (`docs/json-schema-verdict.md`'s verdict: even
where `--json-schema` is used, `response` can come back as several
JSON objects concatenated with no delimiter, which fails `json.loads`
outright). `EventStream.parsed_response()` is therefore a **tolerant
parse**: it returns `None` on any failure — absent, malformed, or
multi-object `response` — rather than raising.
"""
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*$", re.MULTILINE)


@dataclass(frozen=True)
class StepUpdate:
    """One **step update** event — the only liveness signal a **job** has;
    their absence is a **stall** (glossary). `step_type` varies across the
    stream (`user_input`, `agent_response`, `checkpoint`, `tool`, ...), so
    only the fields common to every step update are promoted; `raw` carries
    the full event payload for anything else a caller needs."""

    step_index: Optional[int]
    step_type: Optional[str]
    state: Optional[str]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class EventStream:
    """The typed record parsed from one **event stream**: the
    `conversation_id`, `status`, `response`, `usage`, and `error` carried by
    the **result event** (glossary), plus the ordered **step update**
    sequence observed along the way. `conversation_id` falls back to the
    **init event**'s value if the stream never reaches a **result event**
    (e.g. a truncated capture) — the init event carries `conversation_id`
    before any work happens, per the glossary. `error` is a killed or
    timed-out run's human-readable error string; it is `None` when the
    **result event** carries no `error` key (every `SUCCESS` fixture in
    this repo). On a real ERROR **result event** from a `--print-timeout`
    expiry, `error` reads `"timeout waiting for response"` — see
    `tests/fixtures/error_result/PROVENANCE.md`."""

    conversation_id: Optional[str]
    status: Optional[str]
    response: Optional[str]
    usage: Optional[Dict[str, Any]]
    step_updates: Tuple[StepUpdate, ...]
    error: Optional[str] = None
    structured_output: Optional[Dict[str, Any]] = None

    def parsed_response(self):
        """**Tolerant parse** of `response` as JSON. Returns the parsed
        object (from `structured_output` if present, else parsed from
        `response`), or `None` if absent, malformed, or (per
        `docs/json-schema-verdict.md`) several JSON objects concatenated
        with no delimiter. Never raises."""
        if isinstance(self.structured_output, dict):
            return self.structured_output
        if not self.response:
            return None
        candidate = _extract_json_object(self.response)
        if candidate is None:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None


def parse_event_stream(text):
    """Parse `text` — the raw NDJSON **event stream** `agy` prints under
    `--output-format stream-json` — into an `EventStream` record.

    Each non-blank line is one JSON event object with an `"event"` key of
    `"init"`, `"step_update"`, or `"result"`. Unrecognized event kinds are
    ignored rather than rejected, so a future `agy` adding a new event kind
    degrades gracefully instead of breaking every caller.
    """
    conversation_id = None
    status = None
    response = None
    usage = None
    error = None
    structured_output = None
    step_updates = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        kind = event.get("event")

        if kind == "init":
            # `init.agent` is deliberately never read here, and must not be:
            # it is a verbatim echo of the requested `--agent` argv value,
            # not the agent that actually resolved. A real **agent
            # fallback** run (`tests/fixtures/agent_fallback/
            # 2026-08-02-run1.ndjson`) shows `init.agent` reading back the
            # exact bogus name that was requested, even though the run
            # silently bound a different agent -- and its **result event**
            # reports `status: "SUCCESS"` with no error, so nothing else in
            # the stream flags it either. `init.agent` is therefore never a
            # valid **bind proof**.
            conversation_id = event.get("conversation_id", conversation_id)
        elif kind == "step_update":
            payload = event.get("step_update") or {}
            step_updates.append(
                StepUpdate(
                    step_index=payload.get("step_index"),
                    step_type=payload.get("step_type"),
                    state=payload.get("state"),
                    raw=payload,
                )
            )
        elif kind == "result":
            payload = event.get("result") or {}
            conversation_id = payload.get("conversation_id", conversation_id)
            status = payload.get("status")
            response = payload.get("response")
            usage = payload.get("usage")
            error = payload.get("error")
            structured_output = payload.get("structured_output")

    return EventStream(
        conversation_id=conversation_id,
        status=status,
        response=response,
        usage=usage,
        step_updates=tuple(step_updates),
        error=error,
        structured_output=structured_output,
    )


def _extract_json_object(text):
    """Strip markdown code fence lines, then return the first balanced
    `{...}` object found in what remains (as raw text, not yet parsed), or
    `None` if no balanced object exists. Mirrors
    `companion.review_output._extract_json_object`'s discipline, kept as a
    private, self-contained copy here rather than an import — this module
    stays purely additive, with no dependency on any consumer module."""
    stripped = _FENCE_RE.sub("", text)
    start = stripped.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return None
