---
name: agy-result-handling
description: Internal guidance for presenting agy companion output back to the user
user-invocable: false
---

# Antigravity (agy) Result Handling

When the companion returns `agy` output:
- Preserve the companion's verdict, summary, findings, and next steps structure.
- For review output (`/agy:review` and `/agy:adversarial-review`), present findings first and keep them ordered by priority ([P0] to [P3]).
- Use the file paths and line numbers exactly as `agy` reports them.
- Preserve evidence boundaries. If `agy` marked something as an inference, uncertainty, or follow-up question, keep that distinction.
- If there are no findings, say that explicitly and keep the residual-risk note brief.
- For `/agy:delegate`, do not turn a failed or incomplete `agy` run into a Claude-side implementation attempt. Report the failure and stop.
- For `/agy:delegate`, if `agy` was never successfully invoked or the agent silently fell back, do not generate a substitute answer. Report the error and stop.
- CRITICAL: After presenting review findings, STOP. Do not make any code changes. Do not fix any issues. You MUST explicitly ask the user which issues, if any, they want fixed before touching a single file. Auto-applying fixes from a review is strictly forbidden, even if the fix is obvious.
- If the companion reports malformed output or a failed `agy` run, include the most actionable stderr lines and stop there instead of guessing.
- If the companion reports that setup or authentication is required, direct the user to `/agy:setup` and do not improvise alternate auth flows.
