# Fixture provenance: session_hook capture

The first evidence this project has that the **SessionStart** hook
(`plugins/agy/scripts/hooks/session_start.py`) actually works end to end:
that `AGY_COMPANION_SESSION_ID`, written by that hook into
`$CLAUDE_ENV_FILE`, really does reach a **companion** subprocess during a
real Claude Code session. Every prior statement about this path in the repo
was derived from reading source, never from a driven session.

Captured by `tools/live_session_hook_capture.py`, which drives real,
non-interactive `claude -p --output-format stream-json --verbose` runs. It
spends **Claude tokens only** — no `agy` run of any kind, see "No agy quota
spent" below.

- **Date**: 2026-09-19
- **Claude Code version**: 2.1.278 (`claude --version`; also carried inside
  the captured bytes as the `init` event's `claude_code_version`)
- **agy version**: not relevant and not consulted — `agy` was never invoked.
- **Capture command**:
  ```
  python3 tools/live_session_hook_capture.py
  ```
  which, per probe, ran:
  ```
  claude -p <prompt> \
      --session-id <pinned uuid> \
      --allowedTools 'Bash(python3:*)' \
      --permission-prompts none \
      --output-format stream-json --verbose
  ```
  with `cwd` set to a fresh throwaway git repo the tool created under the
  system temp dir, `stdin` closed (`subprocess.DEVNULL`), `cwd=` and
  `env=` (carrying `CLAUDE_PLUGIN_DATA` pointed at a fresh throwaway
  **state dir** root) passed explicitly to `subprocess.run`.

## Which plugin directory the captured run loaded — measured, not assumed

Both probes loaded the `agy` plugin from:

```
/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy
```

Determined two independent ways, both present in the committed bytes:

1. The `init` system event's own `plugins[]` table, which Claude Code emits
   listing every plugin it loaded and where from:
   ```json
   {"name":"agy","path":"/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy","source":"agy@agy","version":"0.3.0"}
   ```
2. The expanded `${CLAUDE_PLUGIN_ROOT}` inside probe 1's `tool_use` block —
   i.e. the literal bytes of the command `plugins/agy/commands/status.md`
   caused to run:
   ```
   python3 "/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy/scripts/agy_companion.py" status
   ```

**That directory is NOT this mission's worktree.** The plugin is installed
from a directory marketplace whose source is the primary repo checkout
(`~/.claude/plugins/known_marketplaces.json` -> `agy` -> `source.path`), and
`claude -p` was deliberately run with no `--plugin-dir` override so the
capture would exercise Claude Code's real plugin-resolution machinery. This
capture therefore proves the behaviour of the code in the primary checkout,
not of any edit made in a worktree.

What makes it nonetheless load-bearing for this branch, measured at capture
time:

```
$ git -C <primary checkout> rev-parse HEAD   -> 8ed5b81 (epic/modernize-127/integration)
$ git -C <this worktree>    rev-parse HEAD   -> 8ed5b81 (epic/modernize-127/M3)
$ diff -r <primary>/plugins <worktree>/plugins ; echo $?
0                       # no output, exit 0: the two plugin trees are byte-identical
```

and this mission changes nothing under `plugins/`. So the bytes that ran are
the bytes this branch ships. **This is a coincidence of the two checkouts
sitting on the same commit, not a property of the capture**: a future worker
who edits `plugins/` and re-runs this tool without `--plugin-dir` will
capture the OLD code and must not present the result as proof of their
change.

## Hypothesis (a) is refuted: the SessionStart hook DID run

The epic's hypothesis (a) was that the hooks never ran, because Claude Code
loaded the plugin's hook configuration from the stale `0.2.0` marketplace
cache (`~/.claude/plugins/cache/agy/agy/0.2.0/`), which has no `hooks/`
directory at all. The captured bytes refute it directly. Each probe's stream
opens with exactly one `SessionStart` hook pair:

```json
{"type":"system","subtype":"hook_started","hook_id":"7dcdd40b-4d3b-424e-8985-02d316f46dca","hook_name":"SessionStart:startup","hook_event":"SessionStart","uuid":"eb3301c8-b86a-456c-8a50-efc3ca02242f","session_id":"f16ec90e-df67-46ed-a276-52e313bdfced"}
{"type":"system","subtype":"hook_response","hook_id":"7dcdd40b-4d3b-424e-8985-02d316f46dca","hook_name":"SessionStart:startup","hook_event":"SessionStart","output":"","stdout":"","stderr":"","exit_code":0,"outcome":"success","uuid":"88877706-4921-4ceb-b5b1-b97dc4b27f06","session_id":"f16ec90e-df67-46ed-a276-52e313bdfced"}
```

`exit_code: 0`, `outcome: "success"`, empty `stderr`. The `hook_response`
event does not name the owning plugin, so that attribution is established
separately, from three measurements rather than assumed:

- exactly **one** `hook_started`/`hook_response` pair exists in each stream
  (`grep -o hook_started | wc -l` -> `1`), so at most one SessionStart hook
  ran at all;
- the only other SessionStart hook configured on this machine would have to
  come from user settings or another loaded plugin — `~/.claude/settings.json`
  registers only a `PreToolUse` hook, and none of the other plugins the `init`
  event lists as loaded (`claude-hud`, `tars`, `looper`, `agents-md`) ships a
  `hooks/` directory (`ls .../hooks` -> no such directory for each);
- `AGY_COMPANION_SESSION_ID` is a name nothing else in this environment
  writes, and it arrived (next section).

The stale `0.2.0` cache is real — `~/.claude/plugins/installed_plugins.json`
still records `agy@agy` at `0.2.0` — but the `init` event above shows the run
loaded `version: "0.3.0"` from the directory-marketplace source instead. The
version-keyed cache is not in the execution path for a directory marketplace.

## Hypothesis (b) is refuted: the variable DOES reach a companion subprocess

### Probe 2 — the literal value, raw bytes

`2026-09-19-probe2-env-value.stream.ndjson` — the pinned `--session-id` was
`253bdb48-16d1-4309-907f-3d8f40f08474`. The session's `Bash` tool ran a
`python3` subprocess that imported `companion.state` from the loaded plugin
directory and printed the variable `state.SESSION_ID_ENV` names:

```
tool_use   -> python3 -c 'import os, sys; sys.path.insert(0, "/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy/scripts"); from companion import state; print("MEASURED_ENV " + state.SESSION_ID_ENV + "=" + repr(os.environ.get(state.SESSION_ID_ENV)))'
tool_result -> MEASURED_ENV AGY_COMPANION_SESSION_ID='253bdb48-16d1-4309-907f-3d8f40f08474'
```

Not `None`, and not some other value: **exactly** the session id pinned with
`--session-id`. So the SessionStart hook received that id on stdin, wrote it
to `$CLAUDE_ENV_FILE`, and Claude Code exported it into the environment of a
later `Bash` tool subprocess in the same session.

### Probe 1 — the real production invocation, positively discriminated

`2026-09-19-probe1-agy-status.stream.ndjson` — the prompt was literally
`/agy:status`, so the command that ran is the shipped one
(`plugins/agy/commands/status.md`), not a re-derivation of it.

Because `status.run()` uses `AGY_COMPANION_SESSION_ID` only to *scope* which
**job**s it prints, and `build_job_row` never emits the raw session id in any
column, the command's own output cannot leak the value. The probe therefore
turns the scoping into a positive, exact-value discriminator: before the run,
the throwaway repo's **state dir** was seeded with two **job** records —
committed verbatim as `2026-09-19-probe1-seeded-state.json`:

| id | `session_id` | |
|---|---|---|
| `seeded-match-job` | `f16ec90e-df67-46ed-a276-52e313bdfced` | exactly the pinned `--session-id` |
| `seeded-other-job` | `SENTINEL-NOT-THE-CAPTURED-SESSION` | cannot match any real session id |

Both are `completed`, never `running`, deliberately: a `running` job whose
session differs and whose recorded pid probes alive derives as an **orphan**,
and `status._scope_to_session` keeps orphans visible in the session-scoped
view on purpose — which would have destroyed the discriminator.

The captured `tool_result` is, byte for byte:

```
| Job | Kind | Status | Conversation | Elapsed | Stall | Usage | Log tail | Orphan |
|---|---|---|---|---|---|---|---|---|
| seeded-match-job | review | completed | (none yet) | 0s | n/a (terminal) | (none yet) |  |  |
```

`seeded-match-job` present; `seeded-other-job` **absent** — the string
`seeded-other-job` appears nowhere in the whole captured stream. That
asymmetry is only producible by `_scope_to_session` running with
`session_id == "f16ec90e-df67-46ed-a276-52e313bdfced"`. Neither degenerate
case fits: with no value the filter is skipped and *both* rows print; with
any other value *neither* prints and the table collapses to the
`"No agy jobs for the current session in this repo yet."` line, which is also
absent.

## Verdict

Neither (a) nor (b). **The shipped export path works.** The SessionStart
hook runs, exits 0, and the exported `AGY_COMPANION_SESSION_ID` arrives —
with the exact session id — in both an arbitrary `Bash` tool subprocess and
in the **companion** invoked by `/agy:status`. **No code fix was required, so
none was made**, and the committed fixture is a capture of the shipped,
working behaviour rather than of a bug.

## No agy quota spent — structurally, not by promise

Both probes were launched with `--allowedTools 'Bash(python3:*)'`, the same
grant `plugins/agy/commands/status.md`'s own `allowed-tools` frontmatter
declares, plus `--permission-prompts none` ("nobody answers permission
prompts; anything that would prompt is denied automatically"), so the run can
neither invoke a non-`python3` command nor hang waiting for someone to
approve one. No `--dangerously-skip-permissions`,
`--allow-dangerously-skip-permissions` or `--permission-mode
bypassPermissions` was passed, here or anywhere in the capture tool. The one
plugin command exercised, `/agy:status`, reaches `companion/status.py`, whose
module docstring states "Reads only; never writes state, never touches `agy`
or spawns anything". Confirmed against the code rather than taken on the
docstring's word: `status.py` itself never imports `subprocess`, and the one
subprocess it can reach — `companion.git.ensure_git_repository` ->
`subprocess.run(["git"] + args, ...)` at `companion/git.py:195` — only ever
spawns `git`. The captured `tool_use` blocks in both fixtures are the
complete list of commands the sessions ran; neither names `agy`.

## M3b — which conditions produce a present vs. an absent value

M3 (everything above) measured exactly one condition: a fresh headless
`claude -p` run with a **pinned** `--session-id`. It found the variable
present, but it could say nothing about the condition in which the epic
runner had separately observed it **absent**. M3b captured three more
conditions and measured one existing session directly.

- **Date**: 2026-09-19
- **Claude Code version**: 2.1.278 (`claude --version`; also carried inside
  the captured bytes as each `init` event's `claude_code_version`)
- **agy version**: not relevant and not consulted — `agy` was never invoked.
- **Capture tool**: `tools/live_session_env_conditions_capture.py`, which
  reuses `tools/live_session_hook_capture.run_probe` (same throwaway-scratch
  discipline, same `--permission-prompts none`, same `Bash(python3:*)`
  grant; probe 3 adds `Task` to that grant and nothing else).
- **Capture commands** (two runs, both recorded verbatim in
  `2026-09-19-m3b-capture-tool-stdout.txt`):
  ```
  python3 tools/live_session_env_conditions_capture.py --timeout 300
  python3 tools/live_session_env_conditions_capture.py --only subagent --timeout 420
  ```

All three probes loaded the same plugin directory as M3 did, read the same
way — off each `init` event's own `plugins[]` table, never assumed:

```json
{"name":"agy","path":"/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy","source":"agy@agy","version":"0.3.0"}
```

The same caveat M3 states applies unchanged: that is the primary checkout,
not this worktree, and this mission changes nothing under `plugins/`.

### What was captured

| # | condition | `SessionStart` hook that fired | `AGY_COMPANION_SESSION_ID` | raw evidence |
|---|---|---|---|---|
| 1 | fresh `claude -p`, **no** `--session-id` passed | `SessionStart:startup`, `exit_code: 0`, `outcome: "success"`, empty `stderr` | **present**, `'cb6ca8a0-f5a6-4fe0-b0c9-0b036a29c0ed'` — exactly the id Claude Code generated for the run (`init` event's `session_id`) | `2026-09-19-m3b-probe1-fresh-unpinned.stream.ndjson` |
| 2 | `claude -p --resume <probe 1's session id>`, same cwd | `SessionStart:resume`, `exit_code: 0`, `outcome: "success"`, empty `stderr` | **present**, `'cb6ca8a0-f5a6-4fe0-b0c9-0b036a29c0ed'` | `2026-09-19-m3b-probe2-resume.stream.ndjson` |
| 3 | fresh `claude -p`; the value read **twice** in one session — once from a main-thread `Bash` call, once from a `Bash` call inside a `Task` subagent of that same session | `SessionStart:startup`, `exit_code: 0`, `outcome: "success"`, empty `stderr` | **present in both**, `'b2e068eb-25d9-4a5f-88e3-cfba3ff5241d'` for main **and** subagent | `2026-09-19-m3b-probe3-subagent.stream.ndjson` |

Probe 1 answers "does the export depend on the caller pinning the id?" —
**no**: an id Claude Code generated itself arrived just as exactly.

Probe 2 is the first measurement of a non-`startup` **source**.
`plugins/agy/hooks/hooks.json` registers `SessionStart` with no matcher, so
the hook is *configured* to fire for every source; the captured
`hook_name: "SessionStart:resume"` pair is the first evidence that it
actually does. **`hooks.json` was therefore not touched by this mission** —
nothing captured here justifies changing it. One honest limit: because a
resumed session keeps the same session id, the *value* alone cannot
discriminate "the hook re-exported it on resume" from "something else
carried it over". What is measured is that the hook ran under the `resume`
source and exited 0, and that the value was present and correct.

Probe 3's subagent line is genuinely from inside the subagent, not from the
main thread reporting on its behalf: in the committed bytes the subagent's
`Bash` `tool_use` and its `tool_result` both carry
`"parent_tool_use_id":"toolu_01VdDapJ9hFc7paWoLjKsyrV"`, while the
main-thread pair carries `null`. The two measurements come from one session,
so they cannot be confounded by session age, binary version or install
state. **This refutes "the variable does not reach subagents"**, which was
the obvious hypothesis given where the absence was observed.

### The one session where it was measured ABSENT — and the ordering that explains it

Measured directly, from a `Bash` subprocess of the looper worker subagent
running inside the ordinary interactive Claude Code session that produced
this mission:

```
$ python3 -c 'import os; print(repr(os.environ.get("AGY_COMPANION_SESSION_ID")), repr(os.environ.get("CLAUDE_ENV_FILE")))'
None None
```

Both unset. Two facts about that session, each measured:

```
$ ps -o pid=,lstart= -p 11450          # the `claude` process owning this session
11450 Fri Sep 18 18:50:51 2026

$ stat -f '%N birth=%SB' -t '%Y-%m-%d %H:%M:%S %z' plugins/agy/hooks/hooks.json
plugins/agy/hooks/hooks.json birth=2026-09-19 13:20:34 +0200
$ git log --diff-filter=A --format='%H %ci' -- plugins/agy/hooks/hooks.json
8bfc5461553e2ff0b2b8be09640acc3d83c5cb79 2026-09-19 13:20:32 +0200
```

The session's `claude` process started **about 18.5 hours before this
plugin had a `hooks/` directory at all**. The only install cache on this
machine is `~/.claude/plugins/cache/agy/agy/0.2.0/`, which has no `hooks/`
either (`ls .../0.2.0/hooks` -> no such file or directory), and the plugin
is served live from the directory marketplace whose source is the primary
checkout (`known_marketplaces.json` -> `agy` -> `source.path`), so there was
no other `hooks.json` anywhere for that session to have loaded.

**Stated as what it is**: the ordering above is measured, and so is the
absent value. That no `SessionStart` hook ran for that session is the only
explanation consistent with both, but it was **not** observed directly, and
the session transcript cannot be used to observe it. That last point was
checked rather than assumed, with a control: the session transcripts of
probes 1 and 3 (`cb6ca8a0…` and `b2e068eb…`, under
`~/.claude/projects/<scratch slug>/`) contain **no** hook record of any kind,
even though their captured streams show a `SessionStart` hook that ran and
exited 0. Across every recent transcript scanned, the only hook records
Claude Code writes are `PreToolUse` ones (148 of them, 0 `SessionStart`).
So the absence of a `SessionStart` record in the interactive session's
transcript is evidence of nothing — the transcript never carries them.
The process-start ordering is a capture; "the hook did not run" is the
explanation it supports, and is labelled as an explanation deliberately.

### Not tested

- **A genuinely interactive TTY session.** A subagent cannot drive one —
  there is no way from here to start `claude` on a real terminal, type into
  it and read what a tool subprocess saw. So the question "does an
  interactive session behave differently from `claude -p`?" is **not
  tested**, and nothing above should be read as evidence either way. The one
  interactive session measured (previous subsection) is confounded by the
  process-start ordering and cannot separate the two explanations.
- **A session started while the plugin had no `hooks/`, then re-measured
  after the hooks appeared, without restarting.** That is the exact shape of
  the confound above, and reproducing it deliberately would need a plugin
  install/uninstall mid-session. **Not tested.**
- **`SessionStart` sources `clear` and `compact`.** Only `startup` and
  `resume` were captured. **Not tested.**

### Cost and quota

Three `claude -p` runs, Claude tokens only. `agy` was never invoked: probes
1 and 2 ran under the unchanged `Bash(python3:*)` grant, probe 3 under
`Bash(python3:*),Task` — a subagent's own Bash calls are bound by the same
`python3` grant — and every run had `--permission-prompts none` with no
`--dangerously-skip-permissions` or `--permission-mode bypassPermissions`
anywhere. The only command any probe was asked to run is a one-line
`python3 -c` that prints an environment variable; the committed streams'
`tool_use` blocks are the complete list of what actually ran, and none names
`agy`.

### Scrubbing (M3b files)

Same discipline as M3: path redaction only. Username -> `REDACTED`; the two
throwaway scratch roots -> `<SCRATCH>` (and `<SCRATCH-RUN-1>` /
`<SCRATCH-RUN-2>` in the tool stdout, which names both); the enclosing
per-user temp dir -> `<TMPDIR>`. Session UUIDs are **not** redacted — they
are the evidence. All three probes' stderr files were captured and are empty
(0 bytes), so they are not committed.

## Files

| file | what it is |
|---|---|
| `2026-09-19-probe1-agy-status.stream.ndjson` | probe 1's raw `claude -p --output-format stream-json` stdout (10 NDJSON lines) |
| `2026-09-19-probe2-env-value.stream.ndjson` | probe 2's raw stdout (9 NDJSON lines) |
| `2026-09-19-probe1-seeded-state.json` | the **state dir**'s `state.json` as seeded before probe 1 — the experiment's input side |
| `2026-09-19-capture-tool-stdout.txt` | `tools/live_session_hook_capture.py`'s own stdout for this run, including its `PASS` line |
| `2026-09-19-m3b-probe1-fresh-unpinned.stream.ndjson` | M3b probe 1's raw stdout — fresh `claude -p` with **no** `--session-id` (9 NDJSON lines) |
| `2026-09-19-m3b-probe2-resume.stream.ndjson` | M3b probe 2's raw stdout — `claude -p --resume` of probe 1's session (8 NDJSON lines) |
| `2026-09-19-m3b-probe3-subagent.stream.ndjson` | M3b probe 3's raw stdout — the value read from the main thread and from a `Task` subagent of the same session (22 NDJSON lines) |
| `2026-09-19-m3b-capture-tool-stdout.txt` | `tools/live_session_env_conditions_capture.py`'s own stdout for both M3b runs |

Both probes' stderr files were captured too and are **empty** (0 bytes), so
they are not committed.

## Scrubbing

Same discipline as every other fixture here: path redaction only, every other
byte exactly as Claude Code wrote it.

- this machine's username -> `REDACTED`, everywhere it appears — matching
  `tests/fixtures/delegate/PROVENANCE.md`'s `/Users/<user>` ->
  `/Users/REDACTED` convention, which `tests/test_log_fidelity.py`'s
  `FixtureHygieneTest` enforces by glob over every committed `.ndjson`. Only
  the username segment is rewritten, which is why the load-bearing plugin
  path still reads
  `/Users/REDACTED/workspace/code/japan4/work/lab/agy-plugin-cc/plugins/agy`.
- the throwaway scratch root
  `/private/var/folders/<tmp id>/T/agy-session-hook-capture-<suffix>` ->
  `<SCRATCH>`, and the enclosing per-user temp dir -> `<TMPDIR>` (also in the
  `-`-joined form Claude Code uses for its own `memory_paths`).

No credentials, tokens or email addresses appear in these fixtures (checked:
the only `*token*` matches are the `usage` blocks' token counts). The session
UUIDs are deliberately **not** redacted — they are the evidence.
