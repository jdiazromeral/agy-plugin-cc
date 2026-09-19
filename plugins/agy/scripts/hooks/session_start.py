#!/usr/bin/env python3
"""SessionStart hook: exports AGY_COMPANION_SESSION_ID to $CLAUDE_ENV_FILE so
background jobs spawned during this session can be scoped in status/cancel.
"""
import json
import os
import sys


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    session_id = payload.get("session_id")
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file and session_id:
        try:
            with open(env_file, "a", encoding="utf-8") as f:
                f.write('export AGY_COMPANION_SESSION_ID="{}"\n'.format(session_id))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
