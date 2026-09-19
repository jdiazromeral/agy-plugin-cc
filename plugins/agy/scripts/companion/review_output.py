"""companion.review_output — tolerant parse and rendering of agy-review's
stdout into a structured review.

Pure functions, no subprocess, no I/O. The **bind** check is a separate,
prior concern living in `review.py`: by the time text reaches
`tolerant_parse` here, the caller has already decided the stdout is worth
trying to parse as a review at all.

Follows `docs/review-schema-verdict.md`'s ranked defense list, most-observed
first:

  1. `code_location.absolute_file_path` may be relative despite its name
     (run4's fixture proves it) — treated here as opaque display text only,
     never resolved or opened.
  2/3. Bound-but-crashed and silent-fallback are execution errors handled
     upstream in review.py, before this module ever sees stdout.
  4. Markdown fences / prose wrapping — stripped, then the first balanced
     JSON object is extracted.
  5. Missing/extra/mistyped keys — validated; on ANY failure the raw text is
     returned for verbatim rendering rather than erroring or dropping the
     review (a **tolerant parse**, per the glossary).
"""
import json
import re

REQUIRED_TOP_KEYS = frozenset(
    ["findings", "overall_correctness", "overall_explanation", "overall_confidence_score"]
)
REQUIRED_FINDING_KEYS = frozenset(["title", "body", "confidence_score", "code_location"])

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*$", re.MULTILINE)

_PRIORITY_LABELS = {0: "P0", 1: "P1", 2: "P2", 3: "P3"}

# The rendered notice for the **degraded** case: `agy` denied one or more
# tool actions during the run (glossary: **denied action**). Mirrors
# `companion.status.MISSING_SESSION_NOTICE` / `render_status_table`'s
# `session_unknown` pattern (M3b) -- "the degraded case must be visible in
# the rendered output, not merely representable in the data model" applied
# a second time in this codebase. Rendered ahead of the review body
# (findings table OR the raw tolerant-parse fallback text) by
# `render_review` below, never only in `--json`'s `denied_actions` key.
DEGRADED_REVIEW_NOTICE_TEMPLATE = (
    "note: this review is DEGRADED — agy denied {count} tool action(s) "
    "during the run, so the verdict below may be based on incomplete "
    "information: {actions}. `status` still reported the run as SUCCESS; "
    "a denied action is not an execution error (see AGENTS.md's "
    "modernize-127 findings)."
)


def tolerant_parse(text):
    """**Tolerant parse** `text` (agy-review's raw stdout) into a structured
    review. On ANY failure — no JSON object found, malformed JSON, or a
    schema validation failure — returns a fallback result carrying the raw
    text verbatim instead of raising or dropping the review.

    Returns a dict:
      {"ok": True, "findings": [...], "overall_correctness": ...,
       "overall_explanation": ..., "overall_confidence_score": ...}
    or
      {"ok": False, "raw_text": text, "reason": "<why parsing gave up>"}
    """
    candidate = _extract_json_object(text)
    if candidate is None:
        return {"ok": False, "raw_text": text, "reason": "no JSON object found in output"}

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return {"ok": False, "raw_text": text, "reason": "invalid JSON: {}".format(exc)}

    valid, reason = _validate_schema(payload)
    if not valid:
        return {"ok": False, "raw_text": text, "reason": reason}

    return {
        "ok": True,
        "findings": payload["findings"],
        "overall_correctness": payload["overall_correctness"],
        "overall_explanation": payload["overall_explanation"],
        "overall_confidence_score": payload["overall_confidence_score"],
    }


def _extract_json_object(text):
    """Strip markdown code fence lines, then return the first balanced
    `{...}` object found in what remains (as raw text, not yet parsed)."""
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
                return stripped[start:i + 1]
    return None


def _validate_schema(payload):
    if not isinstance(payload, dict):
        return False, "top-level JSON is not an object"
    missing = REQUIRED_TOP_KEYS - set(payload.keys())
    if missing:
        return False, "missing required top-level keys: {}".format(sorted(missing))

    findings = payload["findings"]
    if not isinstance(findings, list):
        return False, "findings is not a list"
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            return False, "findings[{}] is not an object".format(i)
        missing_finding_keys = REQUIRED_FINDING_KEYS - set(finding.keys())
        if missing_finding_keys:
            return False, "findings[{}] missing keys: {}".format(i, sorted(missing_finding_keys))

    return True, ""


def format_priority(priority):
    """Map an int priority 0..3 to its P0..P3 label. Missing/None/unknown
    values render as 'P?' rather than erroring."""
    return _PRIORITY_LABELS.get(priority, "P?")


def render_review(parsed, denied_actions=()):
    """Render a `tolerant_parse` result as a **finding** table with P0-P3
    priorities and the overall correctness **verdict**. When parsing failed
    (`parsed["ok"]` is False), returns the raw text verbatim — per
    AGENTS.md's "a review that renders ugly beats a review that vanishes" —
    rather than erroring or dropping the review.

    `denied_actions` (default empty — every pre-existing caller is
    unaffected) is the run's parsed **result event** `denied_actions`
    collection (glossary: **denied action**). When non-empty, a
    **degraded** notice (`DEGRADED_REVIEW_NOTICE_TEMPLATE`) is rendered
    ahead of the review body — findings table OR raw fallback text alike,
    since a review must never lose the notice just because it also failed
    to parse."""
    if not parsed["ok"]:
        body = parsed["raw_text"]
    else:
        lines = []
        findings = parsed["findings"]
        if not findings:
            lines.append("No findings.")
        else:
            lines.append("| Priority | Finding | Location | Confidence |")
            lines.append("|---|---|---|---|")
            for finding in findings:
                priority = format_priority(finding.get("priority"))
                title = _escape_cell(finding.get("title", ""))
                location = _escape_cell(_format_location(finding.get("code_location")))
                confidence = finding.get("confidence_score", "")
                lines.append(
                    "| {} | {} | {} | {} |".format(priority, title, location, confidence)
                )

        lines.append("")
        lines.append("Verdict: {}".format(parsed["overall_correctness"]))
        lines.append(parsed["overall_explanation"])
        body = "\n".join(lines)

    if not denied_actions:
        return body
    notice = DEGRADED_REVIEW_NOTICE_TEMPLATE.format(
        count=len(denied_actions), actions=_format_denied_actions(denied_actions)
    )
    return "{}\n\n{}".format(notice, body)


def _format_denied_actions(denied_actions):
    """Compact display text for `denied_actions` entries shaped like the
    real capture's (`{"action": "write_file", "display_name":
    "WriteToFile"}}` — see `tests/fixtures/denied_actions/PROVENANCE.md`):
    `"WriteToFile (write_file)"`, comma-joined. Falls back to whatever
    string representation is available for a non-dict or partial entry
    rather than erroring — the notice must render even from a shape this
    module has not seen."""
    parts = []
    for entry in denied_actions:
        if isinstance(entry, dict):
            action = entry.get("action")
            display_name = entry.get("display_name")
            if display_name and action and display_name != action:
                parts.append("{} ({})".format(display_name, action))
            else:
                parts.append(display_name or action or "unknown action")
        else:
            parts.append(str(entry))
    return ", ".join(parts)


def _format_location(code_location):
    """`code_location.absolute_file_path` is best-effort DISPLAY text only
    (it may be relative despite the name — see run4's fixture) — never fed
    to Path.resolve()/open() here or anywhere downstream of this function."""
    if not isinstance(code_location, dict):
        return ""
    path = code_location.get("absolute_file_path", "") or ""
    line_range = code_location.get("line_range") or {}
    start = line_range.get("start")
    end = line_range.get("end")
    if start is not None and end is not None:
        return "{}:{}-{}".format(path, start, end)
    return path


def _escape_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")
