"""companion.agy_log — regexes and helpers for parsing agy's raw
`--log-file` text (klog format): the **bind** check (`_bind_check`) and the
**conversation** uuid lookup (`find_conversation`).

A single source of truth for these patterns matters: AGENTS.md's "Never
trust an agy run that silently fell back" guard depends on the fallback
regex staying identical everywhere it is checked.
"""
import re

FALLBACK_RE = re.compile(r'Agent "[^"]+" not found, falling back to default')
CREATED_CONVERSATION_RE = re.compile(r"Created conversation ([0-9a-fA-F-]+)")
# Never treat `printmode.go:` lines as a positive **bind** trace. Every
# print-mode run emits them, agent-requesting or not — including `delegate`,
# which passes no `--agent` at all (see the real capture at
# tests/fixtures/delegate/2026-07-30-resume-fallback.log). They prove print
# mode ran, never that an agent resolved. CREATED_CONVERSATION_RE is the only
# positive **bind proof**.

# The **completion marker** (see glossary). Not a status source —
# `status.derive_status` reads the **result event**'s `status` field from the
# **event stream**. Its one job is being the only in-log source of a
# **conversation** uuid on a **genuine resume**, which writes no `Created
# conversation` line at all (pinned against real captured bytes by
# tests/test_log_fidelity.py's GenuineResumeTest). Deleting it would leave
# `/agy:status` showing no conversation for a background delegate **job**
# (which stores `conversation: None` at launch) and break the next
# `--resume`. Also read live by `tools/live_delegate_capture.probe_fresh_run`.
COMPLETION_MARKER_RE = re.compile(
    r"Stream completed for ([0-9a-fA-F-]+), clearing ResponsePending"
)


def find_conversation(log_text):
    """The **conversation** UUID this run bound, if the log shows one yet
    (from the `Created conversation <uuid>` bind line, or, failing that,
    the completion marker's uuid). None if neither is present."""
    match = CREATED_CONVERSATION_RE.search(log_text)
    if match:
        return match.group(1)
    match = COMPLETION_MARKER_RE.search(log_text)
    return match.group(1) if match else None


def _bind_check(log_text):
    """Read the agy --log-file text and determine whether the run **bound**
    the requested agent. Tri-state, per docs/review-schema-verdict.md's
    Finding A/B:

      - the fallback line, if present, always wins -> "not_bound";
      - otherwise a `Created conversation <uuid>` line is the ONLY genuine
        positive **bind proof** -> "bound";
      - anything else is zero evidence either way -> "unknown". That
        includes an absent or empty log, and the zero-quota `--model
        <invalid>` probe, which exits on local model validation before any
        conversation is created.

    `unknown` must NEVER be reported as "bound": neither the absence of a
    fallback line nor the presence of `printmode.go:` output proves a bind.

    Returns (state, proof) where state is one of "bound", "not_bound",
    "unknown"."""
    fallback = FALLBACK_RE.search(log_text)
    if fallback:
        return "not_bound", fallback.group(0)
    created = CREATED_CONVERSATION_RE.search(log_text)
    if created:
        return "bound", created.group(0)
    return "unknown", "(no bind trace found: log is empty or carries neither a fallback nor a positive bind signal)"
