#!/usr/bin/env python3
"""SessionEnd hook: cleans up any background jobs still running from this
session when Claude Code exits, preventing orphaned child processes.
"""
import json
import os
import signal
import sys
import tempfile
from pathlib import Path


def cleanup_session_jobs(session_id):
    if not session_id:
        return
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        state_root = Path(plugin_data) / "state"
    else:
        state_root = Path(tempfile.gettempdir()) / "agy-companion" / "state"
    if not state_root.exists():
        return

    for state_file in state_root.glob("*/state.json"):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            jobs = data.get("jobs", [])
            changed = False
            for job in jobs:
                if job.get("session_id") == session_id and job.get("status") == "running":
                    pid = job.get("pid")
                    if pid and isinstance(pid, int):
                        try:
                            os.killpg(pid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    job["status"] = "cancelled"
                    changed = True
            if changed:
                state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            continue


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    session_id = payload.get("session_id")
    if session_id:
        cleanup_session_jobs(session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
