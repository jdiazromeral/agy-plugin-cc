# Delegation Use Cases (`/agy:delegate`)

While `/agy:review` is designed for strict code review, `/agy:delegate` is perfect for tasks where you want to treat Antigravity as a **fire-and-forget asynchronous background worker**. 

Delegating to `agy` is especially powerful when a task would otherwise consume your Claude Code session's context window or requires capabilities (like native multimodal ingestion) that Claude Code doesn't possess.

## 1. Context Management (Large Files & PDFs)

When you need to extract information from a massive document (e.g., a 50-page PDF specification or a massive log file), doing so natively in Claude Code will consume a huge portion of your token budget and context window, slowing down subsequent interactions.

Instead, offload the heavy lifting to `agy` in the background:

```bash
/agy:delegate --background "Read docs/specifications/massive_architecture_v2.pdf and write a concise 1-page markdown summary of the breaking API changes to docs/api_changes_summary.md"
```

This ensures `agy` crunches the massive token count in the background. You can keep working in Claude Code, occasionally checking `/agy:status`, and then read the clean, compact `docs/api_changes_summary.md` when it's done.

## 2. Multimodal Operations (e.g., Video/Audio Extraction)

Antigravity is powered by Gemini, which possesses deep native multimodal capabilities for ingesting video and audio files—capabilities that Claude Code is not primarily designed for. 

If you need an agent to watch a video or listen to a recording and extract technical arguments, delegate it:

```bash
/agy:delegate --background "Use yt-dlp to download the video at https://youtube.com/... then natively ingest the video and extract the core architectural decisions discussed into architecture_notes.md"
```

Because `/agy:delegate` runs with `--dangerously-skip-permissions`, `agy` has the autonomy to download the file, read the binary video file, and write the output report without blocking you or asking for step-by-step permission.

## 3. Broad, Mechanical Refactoring

For wide-sweeping, low-risk chores where you don't need strict, step-by-step verification, delegation is ideal.

```bash
/agy:delegate --background "Search through the tests/ directory and update all the old unittest assertions to use pytest conventions."
```

You throw the task over the wall, keep your complex logic context pristine in Claude Code, and review the resulting diff once the job finishes.
