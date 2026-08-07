# Glossary — ubiquitous language for agy-plugin-cc

Use these terms exactly, in code, tests, commit messages, and user-facing output.
Where a term has a tempting synonym, the wrong word is named explicitly.

## Core nouns

- **agy** — the Antigravity CLI binary. Always lowercase, never "Antigravity CLI"
  in code or identifiers, never "gemini". The user-facing product name is
  "Antigravity CLI"; the binary and every symbol referring to it is `agy`.
- **companion** — the Python program that wraps `agy`
  (`plugins/agy/scripts/agy_companion.py` and its package). Borrowed from
  upstream's `codex-companion`. Not "wrapper", not "runner", not "driver".
- **job** — one tracked `agy` invocation, foreground or background, with an id,
  a kind, a status, and a stored result. Not "task" — `agy` and Claude Code both
  use "task" for their own concepts, and the collision causes real confusion.
- **kind** — what a job does: `review` or `delegate`. Not "type", not "command".
- **conversation** — an `agy` thread, identified by a UUID and resumable with
  `--conversation <uuid>`. Upstream Codex calls this a "thread"; we do not.
  Never "session" — that word is reserved for Claude Code.
- **session** — a Claude Code session, identified by Claude's session id. Jobs
  are tagged with it so `/agy:status` can scope to the current session.
- **state dir** — the per-repository directory under `$CLAUDE_PLUGIN_DATA`
  holding `state.json` and `jobs/`. Keyed by a slug plus a hash of the
  canonicalized repo root.
- **review target** — the resolved thing being reviewed: working tree, staged
  changes, or a branch diff against a base ref. Resolving it is **target
  selection**.
- **manifest** — the list of the repository's tracked files (`git ls-files`)
  spliced into a review prompt so the bound agent, which has no file-access
  tools, can tell a genuinely absent file from one merely outside the diff.
  Not "file list", not "index" — "index" means git's staging area.
- **finding** — one issue reported by a review, carrying a title, body,
  priority (P0-P3), confidence score, and code location. Not "issue", not
  "comment".
- **verdict** — the review's `overall_correctness` result. Not "result", which
  means the stored output of a job.
- **fixture** — raw captured `agy` stdout committed under `tests/fixtures/`, used
  so tests never invoke the real binary.
- **fake agy** — the stub executable injected on `PATH` during tests. Not "mock".

## Core verbs

- **delegate** — hand a task to `agy` to work on. The user-facing command is
  `/agy:delegate`. Upstream calls this "rescue"; we do not, because the word
  implies Claude got stuck, which is only one of the reasons to use it.
- **capture** — run the real `agy` once and record its stdout as a fixture.
  Only the tools under `tools/` capture. Nothing else may invoke the real binary.
- **harvest** — read a finished job's stored output.
- **bind** — which agent `agy` actually used for a run, read back from the log.
  "The run bound the default agent" is the failure we guard against.

## Terms of art specific to this port

- **silent fallback** — `agy`'s behavior when given an unknown `--agent` or
  `--conversation` value: it falls back to a default with no stdout signal, no
  stderr, and exit 0. The single most dangerous behavior in this integration.
  Always guarded, never tolerated.
- **tolerant parse** — the required parsing discipline for model output: strip
  markdown fences, extract the first balanced JSON object, validate, and on any
  failure render the raw text rather than erroring.
- **completion marker** — the log line
  `Stream completed for <uuid>, clearing ResponsePending`, which is how a
  background job is known to have finished. Distinct from process exit.
  Superseded by the **result event** in the `stream-json` epic; the term stays
  here because it names a real thing in `agy`'s log, which the **bind** check
  still reads.
- **event stream** — the NDJSON `agy` emits under `--output-format stream-json`:
  one JSON object per line, `init` → `step_update`(×N) → `result`. Not "output",
  not "the JSON" — those are ambiguous now that a run has both a stream and a
  response inside it.
- **init event** — the stream's first event, carrying the `conversation_id`
  before any work happens.
- **step update** — a progress event emitted as the run proceeds. Their arrival
  is the only liveness signal a **job** has; their absence is a **stall**.
- **result event** — the stream's final event: `status`, `conversation_id`,
  `response`, `num_turns`, `duration_seconds`, and `usage`. The structured
  replacement for the **completion marker** and the crash trace. Its presence
  means the run STOPPED, not that it succeeded — read `status` (`SUCCESS` /
  `ERROR`) and `response`, never presence alone. A killed or timed-out run
  emits a result event with `status: "ERROR"`, an empty `response`, and a
  human-readable `error`. **`usage` on an ERROR result event is not
  reliably "fully populated"** — that claim was written before any real
  ERROR bytes existed and was wrong. Measured against `tests/fixtures/error_result/2026-08-02-run1.ndjson` (a
  `--print-timeout` expiry before any `agent_response` step began): the
  `usage` block is all-zero. A run killed further into generation, with
  output already streamed, might carry partial non-zero counts — that case
  is not witnessed by any fixture in this repo yet, so it is not claimed
  either way.
- **response** — the model's own output, carried inside the **result event**.
  Distinct from the **result**, which is the stored output of a **job**, and
  from the **event stream** that delivers it.
- **stall** — a **job** with no **step update** for a threshold interval. Not
  "hang" and not "crash": a stalled job may still be alive, and there is no exit
  code to consult. The **event stream** carries no per-event timestamp, so a
  stall is inferred from the `output_file`'s mtime — the file has stopped
  growing — not from any event's age.
- **usage** — the token counts on a **result event** (input, output, thinking,
  cache read, total). The only per-job cost signal available.
- **agent workspace** — the directory staged with
  `.agents/agents/<name>/agent.md` and used as a run's cwd, because `agy`
  resolves a workspace-scoped agent only from its own working directory. The
  reviewed repo is attached with `--add-dir` instead, so nothing is written into
  it. Not "scratch dir" — that is any throwaway directory; this one has a
  required shape.
- **degraded** — a capability that exists here but is weaker than upstream's
  because `agy` has no app-server. Used in design docs to mark deliberate,
  understood gaps rather than defects.
- **bind proof** — the specific evidence that a run **bound** the agent it
  asked for. **Nothing in the plugin proves a bind today; it can only
  disprove one.** Say that plainly rather than reaching for a weaker word.
  A positive proof now looks possible but is NOT yet wired in — see the
  `agent=` bullet below.
  - The `--log-file`'s `Agent "<name>" not found, falling back to default`
    line **disproves** a bind. It is reliable and free (it is written before
    local `--model` validation exits), and it is the only bind signal the
    plugin has.
  - The **event stream** proves nothing here. `init.agent` is a verbatim echo
    of the requested `--agent` argv value: a run that fell back reads its own
    bogus request back and reports `status: SUCCESS`. Measured, not inferred
    — `tests/fixtures/agent_fallback/2026-08-02-run1.ndjson`.
  - A `Created conversation <uuid>` line proves a conversation was created,
    not which agent served it. It is the closest thing to a positive trace
    and it is still not a bind proof.
  - Absence of a fallback line is **not** evidence of a bind. Reporting it as
    one was a real shipped defect: `_bind_check` returned `bound` for a run
    that passed no `--agent` at all.
  - **A positive trace exists in the klog and is not yet used.** A live
    capture measured `Starting new conversation (agent=true)` on a
    genuine bind and `(agent=false)` on a requested-but-unresolved agent,
    with BOTH runs passing `--agent` on argv — so the boolean tracks
    RESOLUTION, not argv presence. First positive bind-proof evidence this
    project has had. Until a mission wires it into `_bind_check`, the
    statement above stands: the plugin proves nothing.
  Anything claiming to be a positive bind proof must name the bytes it was
  measured against, or it is inference wearing a proof's clothes.
- **agent fallback** — the **silent fallback** variant where an unknown
  `--agent` value resolves to the default. Distinct from the
  `--conversation` variant, which is a **resume fallback**; they have
  different traces and different structural detectors, and calling both
  "the fallback" has already cost one debugging session.
- **slash-command expansion** — `agy` 1.1.9+ resolves a leading `/` in a
  print-mode prompt as a slash command or skill rather than sending it as
  literal text. The companion sends prompts, never commands, so every
  command vector passes `--disable-slash-commands`. Not "injection" — the
  behavior is documented and intended by `agy`; it is simply not what this
  plugin wants.

## Module layering (added by the `companion-layering` epic)

- **leaf module** — a `companion/` module that imports no subcommand module, and
  is therefore safe for any subcommand to import. `repo.py`, `state.py`,
  `agy_log.py`, `stream_events.py` are leaves. **`cli.py` is not a leaf** — it
  imports all seven subcommands at `cli.py:12-18`, so a subcommand importing back
  from it is a circular import. Shared helpers go in a leaf, never in `cli.py`.
- **log file** — the path passed to `agy --log-file`: its klog trace, carrying the
  fallback line, the conversation id, and the shutdown lines. Read for **bind
  proof** and liveness. Not the **output file**; the two are separate paths on the
  job record and confusing them is what made a lost **result** look like a
  fabricated one.
- **output file** — the on-disk file holding a **job**'s stored **result** (the
  **event stream** for a stream-json run, the forwarded stdout otherwise). Its
  mtime is what a **stall** is inferred from. A job with no output file recorded
  can never be harvested.
- **terminal status** — a **job** status that will not change again: `completed`,
  `cancelled`, or any `error:` variant. The opposite of `running`. A finished
  foreground run that still reports `running` is a **zombie job**.
- **zombie job** — a **job** whose process is long gone but whose row still reads
  `running`, because status is re-derived from a **log file** and an **event
  stream** rather than trusting a recorded **terminal status**. Not "stalled" — a
  **stall** describes a job that may still be alive.

## Words we do not use

- **broker** — upstream's persistent app-server multiplexer. There is nothing to
  broker here. If this word appears in the codebase, something has gone wrong.
- **app-server** — does not exist for `agy`. Referenced only when describing
  upstream.
- **remote control** — `agy --remote-control` is Google's browser/mobile Hub
  companion, not an automation API. Never build on it.
