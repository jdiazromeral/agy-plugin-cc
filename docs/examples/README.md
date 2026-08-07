# Worked examples

Two runnable walkthroughs for `/agy:review`: the happy path (a review that
finds a real bug) and the fail-closed path (what you see when `agy` doesn't
bind the vendored `agy-review` agent). Both use real, previously captured
data — not fabricated sample output — so what's below is what you'd
actually see running these commands yourself.

For examples of when and how to use `/agy:delegate`, see [delegation-use-cases.md](delegation-use-cases.md).

## 1. A bug the review agent catches

`calc-bug/calc_before.py` and `calc-bug/calc_after.py` are the two sides of
this diff:

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

To reproduce it yourself:

```bash
mkdir /tmp/calc-scratch && cd /tmp/calc-scratch && git init -q
cp <this-repo>/docs/examples/calc-bug/calc_before.py calc.py
git add calc.py && git commit -qm "seed"
cp <this-repo>/docs/examples/calc-bug/calc_after.py calc.py
```

Then, from Claude Code with the plugin installed and `cwd` in
`/tmp/calc-scratch`, run `/agy:review`. This is the exact table
`plugins/agy/scripts/companion/review_output.py`'s `render_review` produces
from the real captured response in
`tests/fixtures/review/2026-07-24-run3.stdout.txt` (agy 1.1.6, bound
`agy-review`, `Created conversation 52e2439e-241b-4828-b394-dcf07abe3377`
— see that fixture's `.provenance.md` for full capture details):

```
| Priority | Finding | Location | Confidence |
|---|---|---|---|
| P0 | [P0] Fix incorrect arithmetic operation in add function | /tmp/captures/run3-repo/calc.py:3-3 | 1.0 |

Verdict: patch is incorrect
The patch fundamentally breaks the `add` function by changing its core operation from addition to subtraction, which violates the function's intended and documented behavior.
```

`code_location.absolute_file_path` is best-effort display text from agy
(sometimes relative despite the name — see `docs/review-schema-verdict.md`),
never a path the companion opens itself.

To preview the prompt without spending a live run, use `/agy:review
--dry-run` instead — it resolves and sizes the same diff and prints exactly
what would be sent to `agy`.

## 2. What a failed bind looks like

`/agy:review` only trusts agy's output once it has proof, from that run's
own `--log-file`, that `agy` actually bound the `agy-review` agent. Two
things can go wrong, and both fail closed rather than rendering agy's
output as if it were a real review:

- **Silent fallback** — `agy` was asked for an agent that doesn't resolve
  (e.g. it isn't in this workspace's `.agents/agents/` at all) and silently
  ran its default agent instead, exit 0, no error of its own. This is a
  real, captured failure mode — see
  `tests/fixtures/agent_fallback/PROVENANCE.md`, which quotes the log line
  proving it happened:

  ```
  Agent "definitely-nonexistent-agent-xyz" not found, falling back to default
  ```

  When `/agy:review`'s own bind check finds this line, it prints:

  ```
  error: SILENT FALLBACK — agy did not bind the "agy-review" agent (log: <path>); its output is not a review.
  ```

- **Unconfirmed bind** — the log shows neither a fallback line nor positive
  bind proof (a `Created conversation <uuid>` line). Absence of evidence
  isn't evidence of a bind, so this is treated exactly as cautiously as a
  confirmed fallback:

  ```
  error: UNCONFIRMED BIND — could not verify agy bound the "agy-review" agent (log: <path>); its output is not a review.
  ```

In both cases you get a clear error on stderr and a nonzero exit — never a
findings table built from the wrong agent's output. This is also why
`/agy:setup`'s own bind probe (a free, zero-quota check) can only ever
report `UNKNOWN` rather than confirm a bind: proving a positive bind
requires spending a real run, which is exactly what example 1 above does.
