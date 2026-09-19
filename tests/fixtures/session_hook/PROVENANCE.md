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

## Files

| file | what it is |
|---|---|
| `2026-09-19-probe1-agy-status.stream.ndjson` | probe 1's raw `claude -p --output-format stream-json` stdout (10 NDJSON lines) |
| `2026-09-19-probe2-env-value.stream.ndjson` | probe 2's raw stdout (9 NDJSON lines) |
| `2026-09-19-probe1-seeded-state.json` | the **state dir**'s `state.json` as seeded before probe 1 — the experiment's input side |
| `2026-09-19-capture-tool-stdout.txt` | `tools/live_session_hook_capture.py`'s own stdout for this run, including its `PASS` line |

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
