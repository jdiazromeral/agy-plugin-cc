# AGENTS.md

Guidance for AI agents working in this repository.

## What this repository is

`agy-plugin-cc` is a **Claude Code plugin that delegates work to the Antigravity
CLI (`agy`)** — review code, hand off tasks, track background jobs, all without
leaving Claude Code.

It is a deliberate port of [`openai/codex-plugin-cc`](https://github.com/openai/codex-plugin-cc)
(Apache-2.0), which does the same thing for Codex. Read that plugin before
changing architecture here: the Claude-facing surface, the per-repo state dir,
the job model, and the git diff-sizing logic are all borrowed from it.

The plugin's own scripts are **Python 3, standard library only**. A Claude Code
plugin that needs `pip install` is a broken plugin. `subprocess`, `json`,
`sqlite3`, `pathlib`, `re` cover everything required.

## The one architectural fact that shapes everything

Codex's plugin is not a CLI wrapper — it is a client for `codex app-server`, a
long-lived JSON-RPC process with stateful threads, streaming progress
notifications, a native `review/start` engine, schema-enforced output, and
`turn/interrupt` for clean cancellation.

**`agy` has none of that.** It offers one blocking `agy -p` spawn. Every
degradation in this plugin traces back to that single gap. Do not go looking for
an agy app-server; it does not exist (see "Settled findings" below).

## Settled findings — do not re-derive these

Verified empirically against `agy` 1.1.6 on 2026-07-24. Full evidence lives in
`../../ideas/agy-plugin-cc/` (three research reports).

- **`--remote-control` is NOT an automation API.** It replaces print mode with a
  local web server on `127.0.0.1:4400` and triggers a fresh Google OAuth consent
  flow with broader scopes. Binary strings (`[remote-control].borg.google.com`,
  `AGY_ENABLE_HUB`, `ValidateCorpSSO`) show it is Google's browser/mobile "Hub"
  companion routed through internal Google infra. Undocumented, and useless to us.
- **`agy -p` stdout is clean**: byte-exact raw text, no banner, no ANSI (verified
  with `od -c`). stderr is separate and empty on success. Exit 0 = success,
  1 = hard failure.
- **The conversation ID is recoverable per run.** Pass `--log-file <path>`; the
  log contains `Created conversation <uuid>` and a completion marker
  `Stream completed for <uuid>, clearing ResponsePending`. Parse the log — it is
  the source of truth for both job identity and completion.
- **`--conversation <uuid>` genuinely resumes** (verified by a recall test).
- **The SQLite conversation store is useless to us.** `~/.gemini/antigravity-cli/
  conversations/<uuid>.db` is all-BLOB protobuf with no queryable title, cwd, or
  timestamp. Do not build on it.
- **`~/.gemini/antigravity-cli/cache/last_conversations.json`** maps `cwd → uuid`
  but keeps only the *last* conversation per directory, so concurrent jobs clobber
  each other. Prefer the per-run log file. Superseded, kept here so nobody
  rediscovers it and thinks it is a good idea.
- **A workspace-scoped custom agent only binds in `agy -p` mode with
  `--new-project`.** Every passive placement (`.agents/agents/<n>/agent.md`,
  `.agents/agents/<n>.md`, `.agent/agents/<n>/agent.md`,
  `_agents/agents/<n>/agent.md`, `.gemini/agents/<n>.md`,
  `.agents/plugins/<p>/agents/<n>.md`) silently falls back without it. This is
  why `/agy:review` launches with `--new-project`. Full ledger:
  `docs/review-schema-verdict.md` Finding B.
- **`agy agents` lists only GLOBAL agents.** A workspace-scoped agent (like the
  shipped `agy-review`) never appears there even when it binds — so `agy agents`
  cannot confirm a workspace agent will work. Use the free bind probe instead
  (see Never).
- **Agent resolution is relative to the run's cwd, and the vendored agents do
  not live there.** `agy` looks for `{cwd}/.agents/agents/<n>/agent.md`; the
  plugin ships its agents at `plugins/agy/agents/<n>/agent.md`, a path `agy`
  never reads. `--new-project` is necessary but **not sufficient** — the file
  must also be present under the cwd. Both launch paths therefore run in an
  **agent workspace** staged by `companion.agent_workspace` and attach the
  reviewed repo with `--add-dir`, rather than running in the repo itself. This
  keeps the repo write-free, and works because the diff is already embedded in
  the prompt (Finding C) — the agents need no repo access today. Verified
  against agy 1.1.8: non-git temp cwd + `--add-dir <repo>` binds cleanly.
  A background job's workspace lives under the **state dir**, not a temp dir,
  because it must outlive the process that spawned it.
- **A failed resume logs TWO lines, and only one of them is durable.** agy
  1.1.8 emits `common.go:281] Conversation <uuid> not found, ignoring
  --conversation flag` (uuid **bare**) followed by `Warning: conversation
  "<uuid>" not found.` (uuid **quoted**, human-facing). Match the klog trace,
  not the warning — presentation text is the likeliest to be reworded or
  suppressed. Captured verbatim in
  `tests/fixtures/delegate/2026-07-30-resume-fallback.log`.
- **The resume-fallback trace is FREE to probe**, like the agent bind check:
  conversation resolution is logged before local `--model` validation exits,
  so `--conversation <bogus> --model bogus-model-xyz` captures the whole
  trace with no conversation created and no model call. `make
  check-live-free` runs exactly this. Extends Finding A beyond agent binding.
- **The completion marker is real, and its source file is not what the fake
  said.** Verified live on 1.1.8: `conversation_manager.go:666] Stream
  completed for <uuid>, clearing ResponsePending`. Also note
  `Created conversation` moved from `server.go:934` (1.1.6) to
  `server.go:1007` (1.1.8) — which is precisely why none of these regexes may
  anchor on a file name or line number.
- **Never validate a regex against `tests/fake_agy.py`.** The fake's log lines
  were written from memory and have hidden two shipped defects that way (an
  agent that could never bind, and a fallback guard matching only
  presentation text). `tests/test_log_fidelity.py` holds every pattern
  against real captured logs; add to it rather than to the fake when a new
  trace matters.
- **agy logs `error getting token source: You are not logged into
  Antigravity` during startup even on an authenticated run** that goes on to
  log `Auth succeeded`. It is startup noise, not an auth verdict. Note that
  this exact string is one of `setup.py`'s `_NOT_AUTHENTICATED_MARKERS` — the
  auth heuristic reads `agy agents`' stderr, not a run's `--log-file`, and
  must stay that way or it will report a working install as unauthenticated.
- **agy exits 0.16s after SIGTERM, killed mid-stream** (measured, 1.1.8,
  `killpg` on a detached run generating a long response; exit status 1). It
  does not trap or ignore the signal. `/agy:cancel`'s 5s grace before SIGKILL
  is therefore ~30x headroom, not a tuned guess. It also means a `ps` check
  fired immediately after a non-waiting cancel can still see the process —
  which is exactly how the original "cancel lies about success" bug looked
  when first observed, and why it was nearly dismissed as a false alarm.
- **Never report a cancellation you have not verified.** SIGTERM is a
  request. `cancel` previously wrote `status: cancelled` straight after
  signalling, and because `cancelled` short-circuits `build_job_row`, a
  process that ignored the signal would keep running, keep spending quota,
  and never appear in `/agy:status` again. It now polls for actual exit,
  escalates to SIGKILL, and on total failure leaves the job `running` and
  exits non-zero. Same rule as the **bind** check: verify, then claim.
- **Repo scoping bounds writes, not reads.** Setting `cwd`/`--add-dir` to one
  repo does not stop `agy` from reading outside it: asked to summarize a single
  repo, it read the *workspace-root* `AGENTS.md`/`CONTEXT.md` and summarized the
  whole workspace (smoke-tested 2026-07-08). It walks up parent directories for
  context files the way Claude Code does for `CLAUDE.md`. Treat per-repo scoping
  as a **write** blast-radius bound only — never promise a caller that a run
  cannot read a sibling repo.
- **agy has no native code-review engine.** There is no `review/start` analog.
  The review prompt is ours to supply.
- **The docs lag the binary.** https://antigravity.google/docs/cli/ claims there
  is no `--agent` flag (there is), documents a `--cwd` flag (`--help` does not
  list it), and never mentions `--print-timeout`. **The binary is authoritative.**
- **`agy changelog` is a real, free, authoritative source for upgrade triage.**
  Confirmed live against a local `agy` 1.1.9 binary (2026-08-01): it prints
  real per-version release notes (features, fixes) going back through at
  least 1.1.5, and spends no agy quota. Prefer it over guessing what changed
  between two agy versions or re-deriving release notes from source. It is
  how the two behaviour changes that moved this plugin's floor were found —
  1.1.9 expanding slash commands in print mode, and 1.1.10 fixing `--model`
  / `--effort` in `-p`. **Run it first on every agy upgrade.** Confirmed
  still present as `agy changelog` on 1.1.11, 2026-08-07.
- **`--model` and `--effort` were silently ignored in `agy -p` before
  1.1.10.** The flags parsed and were then applied after model configuration
  had already initialized, so the run used the persisted or default model
  with exit 0 and no diagnostic. `/agy:delegate` forwards both flags
  verbatim, so on an older binary it would run a model the user did not ask
  for. There is no log trace to guard against this, which is why
  `setup.MIN_AGY_VERSION` is a hard floor at 1.1.10 rather than a runtime
  check. Same failure class as the `--agent` and `--conversation` silent
  fallbacks: accepted, ignored, no signal.
- **Read-only slash commands are answered for free in print mode.**
  `agy -p "/<cmd>"` for `<cmd>` in `usage`, `credits`, `model`, `effort`,
  `skills` answers without starting an agent turn — verified against agy
  1.1.11, 2026-08-07. Under `--output-format json` the envelope carries
  `conversation_id: ""`, `num_turns: 0`, and an all-zero `usage` block, with
  the answer itself in a typed `command: {name, data}` field, e.g.
  `{"conversation_id":"","status":"SUCCESS","response":"gemini-3.1-pro-high\tGemini
  3.1 Pro (High)\n","duration_seconds":0,"num_turns":0,"usage":{"input_tokens":0,
  "output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0},
  "command":{"name":"model","data":{"id":"gemini-3.1-pro-high","label":"Gemini
  3.1 Pro (High)","effort":"high","is_default":false}}}`. Plain text output is
  tab-separated records, one per line. This is a **new class of zero-quota
  probe** — the first since the bind-check probe (Finding A) — and strictly
  better than it: it returns a typed positive answer rather than only
  disproving a fallback.
- **`--model` and `--effort` are reflected by the `/model` and `/effort`
  probes.** `agy -p "/model" --model gemini-3.1-pro-low` returns
  `command.data.id == "gemini-3.1-pro-low"`; `agy -p "/effort" --effort low`
  returns `low` — verified against agy 1.1.11, 2026-08-07. Consequence: the
  silent-fallback bug class that forced `setup.MIN_AGY_VERSION` to a hard
  1.1.10 floor (previous entry) can now be **proven per-binary for free**,
  instead of assumed from a version string — the same "structural proof over
  inference" upgrade this repo already made for the resume-bind check.
- **`--disable-slash-commands` defeats the free-probe class, expensively.**
  `agy -p "/usage" --disable-slash-commands` sends `/usage` to the model as
  literal prompt text: it spends quota, creates a conversation, and returns a
  hallucinated answer ("It looks like you're asking for usage information!
  The `/usage` command isn't a recognized slash command...") — verified
  against agy 1.1.11, 2026-08-07. Every other command vector in this plugin
  already carries `--disable-slash-commands` unconditionally, and `setup.py`
  states that as an invariant; state it precisely now that a legitimate
  exception exists: the rule is that **user-supplied text** is never
  slash-expanded, not that the flag is always present. A probe sends a
  plugin-authored constant, never user text, so it is the one place omitting
  the flag is correct. Without this written down, someone will "fix" the
  missing flag back onto a probe and silently start burning quota (see also
  the `Never` rule below).
- **`/credits` is not the quota signal; `/usage` is.** On a subscription
  account `/credits` reports `Remaining credits 0` while `/usage` reports
  100% weekly remaining — different axes, both free per the probe above.
  1.1.11 also fixed a bug where an empty credits response was read as a
  balance of zero. Verified against agy 1.1.11, 2026-08-07.
- **stream-json gained a `command_result` event in 1.1.11.** Under
  `--output-format stream-json`, a read-only slash command emits
  `{"event":"command_result","command":{"name":"model","data":{...}}}`
  before the terminal result event — verified 2026-08-07.
  `companion/stream_events.py` parses a closed-vocabulary event stream; this
  is a new top-level event type to recognize, not yet handled.
- **Interactive-only slash commands now hard-fail in print mode instead of
  falling through.** `agy -p "/clear"` exits **2** with `Error: /clear is
  not available in print mode (every print-mode run already starts a new
  conversation unless --continue or --conversation is passed); pass
  --disable-slash-commands to send /clear to the model as literal text` —
  verified against agy 1.1.11, 2026-08-07. Before 1.1.11 such a command
  silently became literal prompt text and the model answered as though it
  had run. This plugin is already defended by its unconditional
  `--disable-slash-commands` on user text (previous entry), so the change is
  a fact to record here, not an action to take.
- **`--print-timeout` parses as a Go duration and REQUIRES a unit.** Verified
  against agy 1.1.11, 2026-08-07: `agy -p "/model" --print-timeout 290s` and
  `--print-timeout 24h` both exit 0; `--print-timeout 290` (bare integer)
  exits **2** with `invalid value "290" for flag -print-timeout: time:
  missing unit in duration "290"`. A bare `0` is accepted. Any code building
  this flag must always emit an explicit unit suffix —
  `companion.launch._print_timeout_arg` is the one place that does, covered
  by a regression test asserting the emitted value always carries one.
- **agy's undeclared `5m0s` `--print-timeout` default silently capped every
  launch this plugin makes, including background ones.** The plugin's own
  foreground subprocess timeout (`_AGY_TIMEOUT_SECONDS = 300`) coincidentally
  equals agy's `--print-timeout` default exactly, so the two timers raced for
  the same deadline with an undefined winner — a subprocess-timeout win meant
  an unstructured kill instead of agy's own structured `status: "ERROR"` /
  `error: "timeout waiting for response"` result event. Worse, `--background`
  launches are documented throughout this plugin as having no timeout
  ceiling, but agy's 5m default applied there too, since the plugin never
  passed `--print-timeout` at all. Fixed: every foreground launch now passes
  a `--print-timeout` computed to expire ~10s before the subprocess timeout
  (`companion.launch._print_timeout_arg`), and every background launch passes
  an explicit `"24h"` (`companion.launch._BACKGROUND_PRINT_TIMEOUT_ARG`) so
  the plugin's "no ceiling" promise is actually true.
- **`stdin=subprocess.DEVNULL` is required for all foreground `subprocess.run` calls.**
  When running in automated, piped, or subagent test environments where `stdin`
  is not a TTY, `agy agents`, `agy models`, and print mode probes block
  waiting on input unless `stdin=subprocess.DEVNULL` is passed explicitly.
  Omitting it causes intermittent 10s–20s timeouts. Setting it drops probe
  latency to ~0.07s–0.40s.
- **Read-only slash command probes expanded to 10 commands in 1.1.12.**
  In addition to `usage`, `credits`, `model`, `effort`, `skills` (1.1.11),
  `agy 1.1.12` added non-interactive print mode answers for `/permissions`,
  `/hooks`, `/help`, `/changelog`, `/config` — verified live against 1.1.19,
  2026-08-24.
- **`agy models` progress banner moved to stderr in 1.1.12+**.
  In 1.1.11 `Fetching available models...` was emitted to stdout; in 1.1.12+
  the progress text moved to stderr and stdout contains exclusively the
  tab-separated model rows.

## Never

- **Never trust an `agy` run that silently fell back.** Unknown `--agent` and
  `--conversation` values fall back to defaults with **no stdout signal, no
  stderr, and exit 0** — only a log trace. After any run that depends on a
  custom agent or a resumed conversation, confirm from the `--log-file` which
  agent/conversation actually bound (the `Created conversation <uuid>` line
  present, the `Agent "<name>" not found, falling back to default` message
  absent — matches `FALLBACK_RE` in `agy_log.py`; do not cite a
  `printmode.go` line number for this, it has already drifted twice, 1.1.6's
  `:166` to 1.1.8's `:113` to 1.1.9's `:120`). Do NOT rely on `agy agents` to
  pre-check a workspace agent — it lists only global agents. A silently-wrong
  review is worse than a failed one.
- **Never spend a live `agy -p` run just to check whether an agent bound.**
  `--model bogus-model-xyz` makes agy exit 1 on local model validation
  *before* any model call (zero quota), and the fallback/bind trace still
  writes to `--log-file`. `setup.py`'s doctor and `review.py`'s bind check
  already share this probe — reuse it, do not re-derive it a third time. Full
  technique: `docs/review-schema-verdict.md` Finding A.
- **Never send user-supplied text to `agy` without
  `--disable-slash-commands`.** The flag's job is to stop user text from
  being slash-expanded, not to be unconditionally present. The free-probe
  commands (`/model`, `/effort`, `/usage`, `/credits`, `/skills` — see
  "Settled findings") are the one legitimate exception, because they send a
  plugin-authored constant, never user text, and omitting the flag there is
  what makes them free. Adding the flag back onto a probe sends it to the
  model as literal text instead, spending quota for a hallucinated answer.
- **Never let a JSON parse failure swallow a result.** Model output is scraped
  from stdout, not schema-enforced. Parse tolerantly (strip markdown fences,
  extract the first balanced object), and on failure render the raw text. A
  review that renders ugly beats a review that vanishes.
- **Never build a broker.** Upstream's `app-server-broker.mjs` +
  `broker-lifecycle.mjs` (~460 LOC) exist solely to amortize a persistent
  process across calls. `agy -p` is stateless per call. There is nothing to
  amortize; building one would be cargo-culting.
- **Never scope an agy run to the workspace root.** Always one repo directory.
  Note that `--add-dir`/cwd bounds *writes* only — agy walks up to parent
  `AGENTS.md`/`CONTEXT.md` regardless, so read-scoping cannot be guaranteed.
- **Never require a network call or a `pip install` to run the test suite.**
  Tests run against a fake `agy` on `PATH`, never the real binary.
- **Never let `tests/fake_agy.py` emit a shape the real command vector would
  not produce.** Gate every behavior's stdout on the actual argv (see
  `_requests_stream_json`), the way the real binary does. A fake that emits
  what the command never asked for grades the companion against itself: the
  suite stays green while the real path is broken. This shipped once —
  `delegate_background_finished` synthesized an **event stream** for a launch
  built by `_agy_command`, which never requests one.
- **Never write a claim you did not measure.** Provenance files, docstrings,
  glossary entries and this file are read as evidence by everyone downstream,
  and a plausible inference in that voice is indistinguishable from a
  measurement until someone spends a run disproving it. Five such claims have
  shipped here and were later disproved: `init.agent` called "structurally
  stronger than the log-based bind proof"; the printmode bind trace in
  `docs/review-schema-verdict.md` Finding A; `agy changelog` described as
  "reading local binary metadata, no network round trip"; the **result
  event**'s `usage` called "fully populated" on an ERROR (it is all-zero);
  and a `/agy:cancel` finding since retracted. **All five passed review** —
  a reviewer judges contract fidelity and Method compliance, not whether a
  factual claim inside approved prose was ever measured. Write what you ran
  and what it printed, or write "not tested".
- **Never spend a live `agy` run from a `cwd` the tool has not asserted is a
  throwaway.** A capture tool must check its own working directory is under a
  temp dir and fail loudly otherwise, rather than trusting its caller to pass
  the right `cwd=`. A capture's `cwd` once drifted onto this repo itself —
  self-disclosed, verified harmless, and not covered by the existing "never
  scope an agy run to the workspace root" rule, because a sibling repo is not
  the workspace root.

## Method

- **Use the `/tdd` skill.** Red-green-refactor, behavior-first, one vertical
  slice at a time. The parser, log reader, and target selector all have crisp
  input/output contracts, and captured fixtures give you real inputs to write
  tests against before the code exists.
- **Make the red real, and make it visible.** Two failure modes, both seen
  here. (1) A weak assertion can pass against the very bug it targets: a red
  that used `assertIn` on a single-line NDJSON fixture passed anyway, because
  the buggy raw-dump output contained that substring too. When the defect is
  "renders too much", assert what must be ABSENT, not only what must be
  present. (2) Commit the failing test in its own commit, before the
  implementation that makes it pass — a reviewer cannot verify TDD ordering
  from a single squashed commit, and splitting them costs nothing.
- **Replay the committed captures through a suspect function before planning
  a live capture.** `tests/fixtures/` holds real bytes from several command
  shapes, and a fixture captured for one purpose often answers a different
  question for free. A live capture was once planned to prove `_bind_check`'s
  false positive; replaying
  `tests/fixtures/delegate/2026-07-30-resume-fallback.log` (a delegate run,
  which never passes `--agent`) proved it at zero quota. The proof had been
  committed for three days.
- **When moving or renaming a symbol, grep the whole repo for its old
  qualified name — not just the files being relocated.** A rename inside a
  moved set can leave a stale cross-reference in an untouched sibling's
  docstring or comment. This has recurred: `review.py`'s `_diff_text`
  docstring still named `_target_size` though `_diff_text` itself never
  moved; `review._spawn_detached` survived in three docstrings after the
  move; `_bind_check`'s old home was still named in `docs/` and `AGENTS.md`.
  Repeat the repo-wide grep every time — it has found something every time.
- Use the terms in `.looper/knowledge/glossary.md` exactly — in code, tests,
  and user-facing output.
- Claude never talks to `agy`. Claude builds a command line. All markdown
  surfaces (`commands/`, `agents/`) stay thin; logic lives in Python.
- **Append, never reorder, when registering a subcommand** in `cli.py`'s
  `_SUBCOMMANDS` table (or any other shared dispatch table). Two missions
  extending the same table in parallel merge as a trivial union conflict only
  if both append — reordering turns it into a real conflict.
- Return `agy` output verbatim to the user. Do not paraphrase or summarize it.
- Durable state lives on disk under `$CLAUDE_PLUGIN_DATA`, never in session
  memory — a later turn must be able to read a job started in an earlier one.
- **Duplicate small test helpers per file; never import across `tests.*`.**
  Each test module carries its own `_init_repo`/`_git`/`_repo_env` rather
  than importing from a sibling, so a module stays readable and
  independently runnable. Follow it when adding one.
- **A new companion flag has four surfaces for `delegate`, three for the
  reviews.** `add_arguments` in the companion module, the command doc's
  `argument-hint` frontmatter, that doc's prose rules — and, for
  `/agy:delegate` only, `plugins/agy/agents/agy-delegate.md`'s
  "Forwarding rules", which enumerate per flag what actually reaches the
  bash invocation. `review` and `adversarial-review` skip that fourth
  surface (`allowed-tools: Bash(python3:*)`, invoked directly). Miss the
  forwarding rule and the flag parses correctly at every level and
  silently never reaches `agy_companion.py` — and the test suite cannot
  catch it, because the tests drive the companion directly.
- **Patch the imported name in each module's own namespace.**
  `delegate.py` and `review.py` each do a bare `import subprocess`, so a
  test that mocks `subprocess.run` to capture the call under test also
  swallows the git subprocesses those modules run first. Patch each
  module's own imported name (e.g. `mock.patch.object(delegate,
  "ensure_git_repository", ...)`) to isolate it.

## Checks

`make check` must pass before any mission is DONE. It runs the Python test
suite against a fake `agy` binary injected on `PATH`, plus a lint pass.
Declared as an entry below so the DONE gate enforces it mechanically
rather than relying on intake routing it by hand:

- `make check`

**`make check` is green before your mission starts.** The suite is
comprehensive and lint is clean on `main`, so a validator that is only
`make check` has nothing to prove and grades a mission that never ran.
Compose one that also fails on this mission's own gap — a new test
module, a grep for the artifact you must produce — and confirm it is red
before the loop begins.

**Mission-local green is not sufficient.** Re-run `make check` on the
integration branch after every merge, before mirroring any mission's status.
Two missions can each pass their own validator and still contradict each
other — a shared fixture's meaning can shift under a sibling's change, and
the integration branch is the only place that is visible.

Run the suite as `make check`, or `python3 -m unittest tests.<module>` for a
single file — matching the Makefile's own `test` target. Do NOT invoke
`pytest`; it is shadowed by a shell hook in this environment and fails to
spawn. Two missions lost time rediscovering this independently.
