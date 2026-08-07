# `--json-schema` verdict

Status: **VERDICT REACHED (n=1 per cell)** — all four live `agy` runs this
mission's budget allows are spent (4 of 4). The fourfold duplication and
inflated token/time cost the epic preamble reported from a single anecdotal
run is **reproduced under both `--output-format json` and `--output-format
stream-json`**, and is absent from both formats when `--json-schema` is
omitted. The duplication tracks the presence of `--json-schema`, not the
output format — it is not an artifact of stream-json invocation. See
"The verdict itself" below for the full argument and its limits.

## Setup (held fixed across all four runs)

Reused this repo's existing live-capture pattern
(`tools/live_review_capture.py`'s `bootstrap_scratch_repo()`) rather than
inventing a new fixture: a throwaway scratch git repo with a workspace-scoped
`agy-review` agent copied to `.agents/agents/agy-review/agent.md`, and a
one-line seeded bug in `calc.py` (`return a + b` → `return a - b  # bug:
should be a + b`) as the sole uncommitted diff. The diff is embedded directly
in the prompt text (not left for the agent to discover via a tool call),
matching `live_review_capture.py`'s own documented reason: `--sandbox`
soft-denies tool confirmations in headless print mode.

**Diff** (identical bytes in all four runs):

```diff
diff --git a/calc.py b/calc.py
index b2db1f1..79278a0 100644
--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,3 @@
 def add(a, b):
     """Add two numbers."""
-    return a + b
+    return a - b  # bug: should be a + b
```

**Prompt** (identical text in all four runs, `PROMPT_TEMPLATE` from
`tools/live_review_capture.py` with the diff above substituted in):

```
Review the following working tree diff for bugs. Output only the JSON described in your system instructions, nothing else.

```diff
<diff above>
```
```

**Agent**: `agy-review` (`plugins/agy/agents/agy-review/agent.md`), bound via
`--new-project` — the one placement `docs/review-schema-verdict.md` Finding B
proved actually binds a workspace-scoped custom agent in `agy -p` print mode.
All four `--log-file`s show a `Created conversation <uuid>` line with no
`Agent "agy-review" not found, falling back to default` line, so all four
runs genuinely bound `agy-review`, not the default agent.

**Schema passed to `--json-schema`** (runs 2 and 4 only): a JSON Schema
derived directly from `agy-review`'s own documented output schema in
`agent.md` — `findings[]` (each with `title`, `body`, `confidence_score`,
optional `priority`, required `code_location.absolute_file_path` +
`code_location.line_range.{start,end}`), `overall_correctness` (enum),
`overall_explanation`, `overall_confidence_score`, all four top-level keys
required. This lets the comparison isolate "the agent chooses this shape
unprompted" (runs 1, 3) from "the agent is forced into this shape" (runs 2,
4), rather than testing an arbitrary schema.

**Command shape** (only `--output-format` and `--json-schema` vary):

```
agy -p "<prompt above>" --agent agy-review --sandbox --new-project \
    --output-format <json|stream-json> [--json-schema <schema-file>] \
    --log-file <path>
```

Raw stdout/stderr and `--log-file` for all four runs were persisted to a
scratch directory *before* any parsing (same discipline
`live_review_capture.py` follows), at
`/private/tmp/claude-501/-REDACTED-WORKSPACE/REDACTED-SESSION/scratchpad/m8/captures/`.
That path is outside this repo (session scratch) and is not committed — per
the Method, these captures are not meant to become committed test fixtures.
All raw evidence this document draws on is quoted below, not paraphrased.

## Run ledger — 4 of 4 live runs spent

| # | `--output-format` | `--json-schema` | exit | wall time (external) | `num_turns` | `usage.total_tokens` (input/output/thinking/cache_read) | Response schema-conformant? |
|---|---|---|---|---|---|---|---|
| 1 | `json` | no | 0 | 9.31s | 1 | 14365 (12257/2108/1894/0) | Yes — single valid JSON object |
| 2 | `json` | **yes** | 0 | 13.16s | **4** | 55119 (51046/4073/2772/37028) | **No** — 4 concatenated copies, invalid as one JSON document |
| 3 | `stream-json` | no | 0 | 8.37s | 1 | 13542 (12261/1281/1079/0) | Yes — single valid JSON object |
| 4 | `stream-json` | **yes** | 0 | 17.0s | **4** | 72730 (67602/5128/3704/20613) | **No** — 4 concatenated copies, invalid as one JSON document |

Run 4's wall time (17.0s) and shape (`num_turns` 4) match the epic
preamble's single prior anecdotal data point (`num_turns 4, 3592 output
tokens, 17.1s`) closely — this mission reproduces that same behavior
deliberately, under controlled conditions, rather than treating it as a
one-off. The output-token count differs (5128 here vs. 3592 there) because
this is a different diff/prompt/schema pairing than whatever produced the
epic's original number, which was never recorded beyond the three headline
figures.

## Raw evidence per run

### Run 1 — `json`, no schema

`result` field of the single JSON object printed to stdout (this is the
entire stdout payload under `--output-format json`, not an **event
stream** — the format prints one JSON object, not NDJSON):

```json
{
  "conversation_id": "e95563f3-1d00-496d-adc0-8f8cad186a32",
  "status": "SUCCESS",
  "response": "{\n  \"findings\": [ ... one finding, calc.py bug ... ],\n  \"overall_correctness\": \"patch is incorrect\", ... }\n",
  "duration_seconds": 4.714651,
  "num_turns": 1,
  "usage": {"input_tokens": 12257, "output_tokens": 2108, "thinking_tokens": 1894, "cache_read_tokens": 0, "total_tokens": 14365}
}
```

`response` parses as exactly one JSON object with all four required
top-level keys (`findings`, `overall_correctness`, `overall_explanation`,
`overall_confidence_score`). `code_location.absolute_file_path` is
`"calc.py"` — relative, not absolute, matching the same deviation
`docs/review-schema-verdict.md` already documented for the unenforced case.

### Run 2 — `json`, `--json-schema`

Top-level object gained a `json_schema` field (the schema echoed back) and
`num_turns` jumped to 4:

```json
{
  "conversation_id": "2bd90999-777c-4e7c-b50a-a68fea0ceff2",
  "status": "SUCCESS",
  "response": "{...finding A...}\n{...finding A again, verbatim...}\n{...finding A again...}\n{...finding A again...}\n",
  "duration_seconds": 9.017017,
  "num_turns": 4,
  "json_schema": { "...the schema passed on the command line, echoed back..." },
  "usage": {"input_tokens": 51046, "output_tokens": 4073, "thinking_tokens": 2772, "cache_read_tokens": 37028, "total_tokens": 55119}
}
```

`code_location.absolute_file_path` in this run is a genuine absolute path
(`/private/tmp/.../scratchpad/m8/repo/calc.py`), unlike run 1 — one
schema-conformant field improved, but the top-level `response` string as a
whole is **not** valid JSON:

```
>>> json.loads(response)
json.decoder.JSONDecodeError: Extra data: line 21 column 1 (char 801)
```

`response` is four back-to-back copies of the same JSON object,
concatenated with no separator, wrapping array, or newline-delimited framing
— each individual copy, read in isolation, does satisfy the schema; the
`response` field as actually returned to a caller does not, because nothing
in `--output-format json` frames or delimits multiple objects. This is a
sharper (and less flattering) finding than the epic preamble's "flawless
schema conformance emitted four times over" — the model does hit the schema
each individual time, but the CLI hands the caller something that fails
`json.loads` outright.

### Run 3 — `stream-json`, no schema

The **event stream** (6 lines: 1 **init event**, 4 **step update**s, 1
**result event**) shows exactly one turn, zero tool calls:

```json
{"event":"init","conversation_id":"e9f26852-...","init":{"cwd":"...","agent":"agy-review", ...}}
{"event":"step_update","step_update":{"conversation_id":"e9f26852-...","step_index":0,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{"conversation_id":"e9f26852-...","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"{...partial..."}}
{"event":"step_update","step_update":{"conversation_id":"e9f26852-...","step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"...rest...","duration_seconds":2.494174,"usage":{"input_tokens":12041,"output_tokens":1277,"thinking_tokens":1079,"cache_read_tokens":0,"total_tokens":13318}}}
{"event":"step_update","step_update":{"conversation_id":"e9f26852-...","step_index":2,"state":"DONE","step_type":"checkpoint","duration_seconds":1.146201,"usage":{"input_tokens":220,"output_tokens":4,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":224}}}
{"event":"result","result":{"conversation_id":"e9f26852-...","status":"SUCCESS","response":"{...one finding...}","duration_seconds":3.65786,"num_turns":1,"usage":{"input_tokens":12261,"output_tokens":1281,"thinking_tokens":1079,"cache_read_tokens":0,"total_tokens":13542}}}
```

Only one `user_input` **step update** (the whole run is one turn), no `tool`
**step update**s (the model never called a tool — the diff embedded in the
prompt was enough), and the **result event**'s `response` parses cleanly as
one JSON object.

### Run 4 — `stream-json`, `--json-schema`

26 lines in the **event stream**: 1 **init event**, then **four** separate
`user_input` **step update**s (step indices 0, 7, 9, 11), each starting a
new turn — two of those turns also contain `tool` **step update**s
(`find_by_name`, then `view_file` against `calc.py`) that **do not appear
at all** in run 3's unenforced **event stream**:

```json
{"event":"step_update","step_update":{"conversation_id":"779a2e2b-...","step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"find_by_name","tool_info":{"name":"find_by_name","parameters":{"Pattern":"calc.py","SearchDirectory":"/private/tmp/.../repo"}}}}
{"event":"step_update","step_update":{"conversation_id":"779a2e2b-...","step_index":2,"state":"DONE","step_type":"tool","tool_name":"find_by_name","duration_seconds":0.016112,"tool_info":{...,"output":"calc.py"}}}
...
{"event":"step_update","step_update":{"conversation_id":"779a2e2b-...","step_index":5,"state":"ACTIVE","step_type":"tool","tool_name":"view_file","tool_info":{"name":"view_file","parameters":{"AbsolutePath":"/private/tmp/.../repo/calc.py"}}}}
{"event":"step_update","step_update":{"conversation_id":"779a2e2b-...","step_index":5,"state":"DONE","step_type":"tool","tool_name":"view_file","duration_seconds":0.012423,"tool_info":{...,"output":"4 lines, 83 bytes"}}}
```

The four `user_input` **step update**s (indices 0, 7, 9, 11) with no visible
new prompt text in the event stream strongly suggest `agy` itself is driving
a 4-turn self-correction loop internally when `--json-schema` is present —
not the caller re-prompting. The final **result event**:

```json
{"event":"result","result":{"conversation_id":"779a2e2b-...","status":"SUCCESS","response":"{...finding...}\n{...finding again...}\n{...finding again...}\n{...finding again...}\n","duration_seconds":12.808685,"num_turns":4,"usage":{"input_tokens":67602,"output_tokens":5128,"thinking_tokens":3704,"cache_read_tokens":20613,"total_tokens":72730}}}
```

Same failure as run 2: `response` is four concatenated copies of the same
finding, and fails `json.loads` outright:

```
>>> json.loads(response)
json.decoder.JSONDecodeError: Extra data: line 21 column 1 (char 891)
```

## The verdict itself

**Sample size: n=1 per cell, 4 cells, 4 live runs total.** No repetition
within a cell was budgeted or spent — this mission's entire live-run ceiling
was 4, by contract, and it is fully spent. Nothing below should be read as
generalizing beyond "this fixed diff, this fixed agent, this fixed schema,
observed once per format/schema combination."

**What the four runs show, held against the epic's question** ("inherent to
`--json-schema`, or an artifact of a particular invocation"):

- The fourfold `num_turns` inflation, the fourfold `response` duplication,
  and the multiple-times-higher token/time cost appear **only** when
  `--json-schema` is passed (runs 2 and 4), and appear **identically in
  both `--output-format json` and `--output-format stream-json`** (`4`
  turns, concatenated duplicate `response`, in both). It does not appear in
  either format when `--json-schema` is omitted (runs 1 and 3, both
  `num_turns: 1`, both single-copy valid `response`).
- This directly answers the epic's question, at n=1 confidence: **the
  behavior tracks `--json-schema`, not `--output-format`.** It is not an
  artifact of invoking `agy` under `stream-json` specifically — the epic
  preamble's original run happened to use `stream-json`, but this mission's
  run 2 (`--output-format json`) reproduces the same `num_turns: 4` /
  duplicated-`response` shape without stream-json in the picture at all.
- Run 4's tool calls (`find_by_name`, `view_file`) that run 3 never makes is
  a second, independent signal pointing the same direction: something about
  `--json-schema` changes the run's internal control flow (more turns, and
  in this run's case, extra grounding steps), not just its output framing.
- The epic preamble's characterization ("flawless schema conformance
  emitted four times over") is **not quite right**, and this mission's
  sharper finding supersedes it: each of the four duplicate segments is,
  individually, schema-conformant, but the `response` field as actually
  handed to the caller is **not** valid JSON in either enforced run (`Extra
  data` at `json.loads` time) — the four segments are concatenated with no
  delimiter, wrapping array, or NDJSON framing. A consumer who did
  `json.loads(result["response"])` expecting the schema to hold would get
  an exception, not the intended object, in 2 of 2 enforced runs observed.

**What would change this verdict:**

- A different prompt shape. This mission's prompt explicitly tells the
  agent "Output only the JSON described in your system instructions,
  nothing else" and the `agy-review` agent's own system prompt (`agent.md`)
  *also* independently mandates the same unwrapped, unfenced JSON shape
  that the `--json-schema` schema was derived from. That double
  specification (system prompt says "emit this JSON" and `--json-schema`
  says "conform to this JSON") is a plausible trigger for the four-turn
  self-correction loop seen here — an agent with no such prior JSON
  instruction in its system prompt, given only `--json-schema` to constrain
  a naturally free-form task, might behave differently. This mission did
  not test that variant; it would cost a 5th and 6th live run this
  mission's budget does not have.
- A schema written differently — e.g. one that does not overlap with
  content the system prompt already produces (this mission's schema was
  deliberately derived from `agy-review`'s own documented schema, per the
  steering note, specifically to make this a fair "forced into this shape"
  vs. "chooses this shape" comparison — but that choice may itself be what
  produces the duplication, if the redundancy between system prompt and
  schema is what drives `agy` into multiple corrective turns).
- A larger or differently-shaped diff/task. This mission used the smallest
  possible fixture (one seeded one-line bug in a 4-line file) to hold "one
  fixed diff" trivially constant across all four runs; whether the fourfold
  behavior scales, shrinks, or disappears against a larger diff or a task
  with genuinely multiple findings is untested.
- Repetition within a cell. n=1 per cell cannot distinguish "this is
  `agy`'s deterministic behavior for this input" from "this happened to
  occur once"; a follow-up mission with a larger live-run budget (this
  mission's epic already earmarks that judgment to a later mission per its
  preamble) could re-run each cell 2-3× to check consistency before any
  code changes are made on the strength of this finding.

## Out of scope (explicit, per contract)

This document is a measurement and a verdict only. No code under
`plugins/agy/` was touched, `--json-schema` was not wired into
`agy_companion.py` or any command surface, and no recommendation is made
here about whether or how a later epic should adopt `--json-schema` — that
judgment belongs to the epic preamble's named later mission, which should
read this document's "What would change this verdict" section before
proposing a schema or prompt design.
