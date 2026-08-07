# Review schema verdict

Status: **VERDICT REACHED** — two agy-review **fixture**s were **captured**,
both **bound** and proven so from the `--log-file`, both valid top-level JSON
matching the required schema keys, and one of the two carries a concrete,
quoted schema deviation. The mission's live-run ceiling (6) is fully spent;
this document is final for M2.

Iteration 1 concluded BLOCKED, believing a workspace-scoped custom agent can
never **bind** in `agy -p` print mode. That conclusion was correct as far as
it went — five placements really do all silently fall back — but incomplete:
a sixth placement, `--new-project`, does bind. This document supersedes the
BLOCKED verdict; the disproof and the two findings that produced it
(Finding A, Finding B below) are preserved here because M4 and M8 both need
them.

## Capture ledger (cumulative across the whole mission — final)

Ceiling: 6 live `agy -p` runs total. Spent: **6 of 6**. Free probes
(`agy agents`, `agy models`, `agy --version`, and any `--model
bogus-model-xyz` probe) do not count and are listed separately under
Finding A/B below, not in this table.

| # | Purpose | `--agent` requested | Placement / flags | Bind proof (quoted from `--log-file`) | Bound? | stdout | Valid fixture? |
|---|---------|---------------------|---------------------|-----------------------------------------|--------|--------|-----------------|
| 1 | Decisive bind probe, single candidate path matching the binary's literal template `{workspace}/.agents/agents/{agent_name}/` | `agy-review` | `.agents/agents/agy-review/agent.md`, no `--new-project` | `W... printmode.go:166] Agent "agy-review" not found, falling back to default` | **No — silent fallback** | 399 bytes | No |
| 2 | Bind probe across 4 documented candidate placements simultaneously | `agy-review` | 4 placements, no `--new-project` | `W... printmode.go:166] Agent "agy-review" not found, falling back to default` | **No — silent fallback** | 0 bytes | No |
| 3 | First real capture attempt with `--new-project` (iteration 2), agent.md still declared `tools: [read_file, grep_search, glob]` | `agy-review` | `.agents/agents/agy-review/agent.md` + `--new-project` | `server.go:934] Created conversation a1f7feea-b4df-450c-b82b-fabc1bb2979e`; no fallback line present | **Yes — bound** | 0 bytes, exit 1 | No — run crashed before any model output: `failed to construct executor: no tool converter registered for read_file` (Finding C) |
| 4 | Retry after removing `tools:` from agent.md's frontmatter | `agy-review` | same, `--new-project` | `server.go:934] Created conversation 17debd67-a8ec-437b-85e1-c47a27ff76df`; no fallback line present | **Yes — bound** | 948 bytes, exit 0, schema check passed | No — **valid response but lost**: the capture tool printed only byte-count stats, never wrote the bytes to disk. Logged here per the Method's requirement to log every run including "useless" ones. Tool fixed immediately after (now writes `--stdout-file` before printing anything) |
| 5 | Capture with the stdout-persistence fix in place | `agy-review` | same, `--new-project` | `server.go:934] Created conversation 52e2439e-241b-4828-b394-dcf07abe3377`; no fallback line present | **Yes — bound** | 987 bytes, exit 0, schema check passed | **Yes** — `tests/fixtures/review/2026-07-24-run3.stdout.txt` |
| 6 | Second capture, same setup, to get a consistency data point | `agy-review` | same, `--new-project` | `server.go:934] Created conversation b49d685e-3756-4552-b71d-113dfacc9e2f`; no fallback line present | **Yes — bound** | 757 bytes, exit 0, schema check passed | **Yes** — `tests/fixtures/review/2026-07-24-run4.stdout.txt` |

Full per-fixture provenance (prompt used, conversation UUID, bound agent,
model resolved) lives in the sidecar next to each fixture:
`tests/fixtures/review/2026-07-24-run3.provenance.md` and
`2026-07-24-run4.provenance.md`.

## Finding A — the free bind probe (`--model bogus-model-xyz`)

`agy` validates `--model` locally before ever calling a model. Passing
`--model bogus-model-xyz` makes it exit 1 with `invalid model selection`
after the agent-resolution step has already run and been written to
`--log-file`, but before any `streamGenerateContent`/`Created conversation`
call — verified directly: neither probe log below contains `Created
conversation` or `streamGenerateContent`. This makes it a genuine
zero-quota **bind** probe.

Read the log for exactly one of two signatures:

- `W... printmode.go:166] Agent "<name>" not found, falling back to
  default` present → **silent fallback**, did not bind.
- ~~That line absent, and the log otherwise contains `printmode.go` lines
  (proving agent resolution was reached) → **bound**.~~ **Superseded, measured against a real committed capture.** The two
  probe runs directly below this section really did carry `printmode.go`
  lines whenever agent resolution ran — that observation stands. What does
  not stand is treating "carries `printmode.go` lines" as proof agent
  resolution ran AT ALL: `printmode.go:` lines (e.g. `Print mode:
  starting`) are written by every print-mode run, including one that never
  requested `--agent` in the first place. `tests/fixtures/delegate/2026-07-30-resume-fallback.log`
  — a real `delegate` capture, `delegate.py` never passes `--agent` — has
  exactly this shape (`printmode.go` lines, no fallback line, no `Created
  conversation` line) despite nothing being resolved. `_bind_check`
  (`companion/review.py`) no longer treats the absent-fallback-plus-printmode
  shape as a positive **bind proof**; it reports `"unknown"`. The `Created
  conversation <uuid>` line is the only signature that still proves
  **bound**.

Verified in this mission (both under `--new-project`, see Finding B):

- `--agent agy-review --model bogus-model-xyz --new-project`: log has
  `printmode.go:108`, `:154`, `:330`, `:332`, `:209` (model-validation exit)
  and **no** `printmode.go:166` fallback line → bound.
- `--agent totally-bogus-xyz --model bogus-model-xyz --new-project`
  (control, same scratch repo, same run second later): log has the same
  `printmode.go` lines **plus**
  `W... printmode.go:166] Agent "totally-bogus-xyz" not found, falling back
  to default` → fallback, as expected. This confirms the probe actually
  discriminates bound vs. not, rather than always reporting "bound" because
  resolution never runs.

Neither probe run counts against the live-run ceiling (no model call, no
quota spent, no conversation created).

## Finding B — `--new-project` is required for a workspace-scoped agent to bind

All six documented passive placements tried across this mission's two
iterations failed identically:

```
.agents/agents/<n>/agent.md                  -> FALLBACK
.agents/agents/<n>.md                        -> FALLBACK
.agent/agents/<n>/agent.md                   -> FALLBACK
_agents/agents/<n>/agent.md                  -> FALLBACK
.gemini/agents/<n>.md                        -> FALLBACK
.agents/plugins/<p>/agents/<n>.md            -> FALLBACK
```

Adding `--new-project` to the same `.agents/agents/<name>/agent.md`
placement is the one change that makes it bind, verified with the Finding A
free probe (`--agent agy-review` binds, `--agent totally-bogus-xyz` falls
back, both under `--new-project`, both in the same scratch repo) and then
independently reconfirmed by every one of the four real captures in ledger
rows 3-6, all of which show a genuine `Created conversation` bind with no
fallback line. `--new-project` was reproduced from a fresh throwaway repo
each time (`bootstrap_scratch_repo` in `tools/live_review_capture.py`
`git init`s a brand new repo per run), so this is not leftover state from an
earlier run — it is `--new-project` doing the work.

Constraint compliance: the agent definition is placed inside the throwaway
scratch repo only (`<scratch>/.agents/agents/agy-review/agent.md`), never
under `~/.gemini/agents/`. `--new-project` registers a *project* under
`~/.gemini/config/projects/<uuid>.json` — this is agy's own ordinary
per-run bookkeeping (project ID tracking), not agent registration, and
`~/.gemini/agents/` was confirmed to still contain only `code-auditor.md`
after every run this mission made (see the end of this document).

## Finding C — a `tools:` list naming unregistered converters crashes the run

The vendored `agent.md`, as first written, carried
`tools: [read_file, grep_search, glob]`, copied verbatim from the one known
global agent example (`~/.gemini/agents/code-auditor.md`). Ledger row 3 shows
that under `agy -p --sandbox --new-project`, this makes agy fail *before any
model call*:

```
E... log.go:398] failed to construct executor: no tool converter registered for read_file
E... log.go:398] no tool converter registered for read_file
E... printmode.go:272] Print mode: run ended with error and no response: Agent execution terminated due to error.
```

Exit 1, 0-byte stdout, but the agent had genuinely **bound** (a conversation
was created, no fallback line) — this is a bound run that is not a valid
fixture, logged per the Method's instruction to log every run including
failed ones. The fix applied: the `tools:` field was dropped from
`plugins/agy/agents/agy-review/agent.md` entirely. M2's capture strategy
already embeds the diff directly in the prompt (see the Method's note about
`--sandbox` soft-denying tool calls), so agy-review does not need file-access
tools to do this mission's job. All four subsequent runs (rows 3-6, after the
fix landed before row 4) used the tools-free agent.md and none hit this
failure again. Whether `read_file`/`grep_search`/`glob` are valid converter
names in some *other* agy execution mode (interactive, non-sandboxed) is
untested — M3, if it ever wants agy-review to explore files on its own
rather than being handed a diff, must re-investigate this before restoring a
`tools:` list.

## The schema verdict itself

**Sample size: 2 bound, schema-passing captures** (ledger rows 5 and 6), plus
one bound-but-crashed run (row 3, not schema-relevant — it never reached the
model) and one bound-and-valid-but-lost run (row 4, tooling bug, content
never persisted). This is a small sample; the claims below are stated at
that confidence level, not higher.

**What held in both captures:**

- Both were valid, parseable JSON with no wrapping at all: no markdown code
  fences (no ` ```json `), no prose preamble, no prose postamble. stdout was
  exactly the JSON object, byte-for-byte (see the two fixture files —
  `od -c` on both shows no ANSI, no BOM).
- Both had all four required top-level keys: `findings`, `overall_correctness`,
  `overall_explanation`, `overall_confidence_score`.
- Both found the seeded bug (`calc.py`'s `add` returning `a - b` instead of
  `a + b`) with `overall_correctness: "patch is incorrect"` and a `[P0]`
  finding — the model reliably identifies an obvious, unambiguous bug and
  tags it correctly.
- Both `findings[]` entries had well-typed `confidence_score` (float),
  `priority` (int), and a `code_location` object with `line_range.start`/
  `.end` (ints).

**Deviation actually observed** (ledger row 6,
`tests/fixtures/review/2026-07-24-run4.stdout.txt`):

```json
"code_location": {
  "absolute_file_path": "calc.py",
  "line_range": { "start": 3, "end": 3 }
}
```

`absolute_file_path` is `"calc.py"` — a **relative** path, not absolute,
despite the field's name and the vendored prompt's explicit instruction
("The `code_location` field is required and must include
`absolute_file_path`"). Run 5's equivalent field
(`tests/fixtures/review/2026-07-24-run3.stdout.txt`) *was* a genuine
absolute path (`/tmp/captures/run3-repo/calc.py`). Same prompt, same
diff, same agent, back-to-back runs, different result for this one field —
the model does not reliably honor the "absolute" contract for this field
even when told to.

**Not observed in this sample** (absence noted, not claimed impossible):
markdown-fenced JSON, prose preamble/postamble around the JSON, missing
top-level keys, wrong JSON types for the documented fields, or extra
undocumented keys. With only 2 data points, their absence here is weak
evidence of their absence in general — M4 should still defend against them,
per the recommendation below and per `AGENTS.md`'s existing "never let a
JSON parse failure swallow a result" rule.

## Recommendation to M4

Build the **tolerant parse** to survive, in priority order (most-observed
first):

1. **A `code_location.absolute_file_path` that is not actually absolute.**
   This is the one deviation this mission's sample directly observed and
   quoted above. Do not assume the field is a real absolute path usable for
   `Path.resolve()` or file opening without validation; treat it as
   best-effort display text, and if the tolerant parse or a downstream
   consumer needs a real path, resolve it relative to the reviewed repo root
   as a fallback rather than erroring.
2. **Bound-but-crashed runs that never reach the model at all** (ledger row
   3): exit nonzero, 0-byte stdout, no JSON to parse. The companion must
   distinguish this from "no findings" — surface it as an execution error,
   not an empty review. This is a direct consequence of Finding C
   (`tools:` declarations naming unregistered converters); a future
   agy-review revision restoring a `tools:` list must be re-verified against
   this failure mode before shipping.
3. **The silent fallback** (ledger rows 1-2, and `AGENTS.md`'s existing
   "Never" rule): exit 0, some text that is not necessarily related to the
   review schema at all, no stdout/stderr signal that fallback occurred.
   Verify the bind from the log before trusting stdout as a review at all —
   this is a job-identity check that must happen before parsing, not a
   parse-tolerance concern per se.
4. **Markdown fences and prose wrapping.** Not observed in this mission's 2
   captures, but this is exactly the class of behavior a stdout-scraping
   integration must defend against on general principle (`AGENTS.md`'s
   existing rule), and 2 samples is nowhere near enough to rule it out for
   other prompts, diffs, or models. Strip fences, extract the first balanced
   JSON object, and only then validate.
5. **Missing/extra/mistyped keys.** Also not observed, also not ruled out.
   Validate the four required top-level keys and the shape of each
   `findings[]` entry; on any validation failure, render the raw text
   instead of erroring (per `AGENTS.md`'s existing "never let a JSON parse
   failure swallow a result" rule) rather than crashing or silently dropping
   the review.

If a future mission (M4, or a re-verification via `make check-live`) gets a
larger sample, re-derive this list from that evidence rather than treating
this document's ranking as permanent — it is explicitly a small-sample
finding, stated honestly as such throughout.

## `~/.gemini/` mutation check

`~/.gemini/agents/` was checked after every live run this mission made and
contains only `code-auditor.md` (851 bytes) — `agy-review` was never
registered globally. `--new-project` does write
`~/.gemini/config/projects/<uuid>.json` per run (one project registration
per scratch repo used); that is agy's own ordinary per-run project
bookkeeping, not an agent registration, and is explicitly allowed by this
mission's constraints ("Never mutate `~/.gemini/` beyond agy's own ordinary
per-run bookkeeping").
