#!/usr/bin/env python3
"""agy_companion — thin entry point for the companion.

All logic lives in the `companion` package alongside this file; this script
only puts that package on sys.path and dispatches to it. Invoke as:

    python3 plugins/agy/scripts/agy_companion.py setup [--json]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from companion.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
