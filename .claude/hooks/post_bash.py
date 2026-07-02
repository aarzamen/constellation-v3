#!/usr/bin/env python3
"""PostToolUse:Bash hook (Phase 0, Group 3) — TESTED/EDITED flag wiring.

Design choice (spec left it open): the TESTED flag is maintained here rather
than in guard_bash.py because only PostToolUse sees the command's OUTPUT —
a PreToolUse hook cannot know whether pytest actually passed.

When a Bash command ran pytest and its summary line shows passes with no
failures/errors: touch .claude/flags/TESTED and remove .claude/flags/EDITED.
Always exits 0 — this hook observes, it never blocks.
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get('CLAUDE_PROJECT_DIR',
                                  Path(__file__).resolve().parent.parent.parent))
FLAGS = PROJECT_DIR / '.claude' / 'flags'

SUMMARY_RE = re.compile(r'=+ (.*) =+')


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = (event.get('tool_input') or {}).get('command') or ''
    if 'pytest' not in command:
        sys.exit(0)

    response = event.get('tool_response') or {}
    if isinstance(response, dict):
        output = (response.get('stdout') or '') + (response.get('stderr') or '')
    else:
        output = str(response)

    verdict = None
    for m in SUMMARY_RE.finditer(output):
        verdict = m.group(1)  # keep the LAST summary line
    if not verdict:
        sys.exit(0)
    if 'passed' in verdict and 'failed' not in verdict and 'error' not in verdict:
        FLAGS.mkdir(parents=True, exist_ok=True)
        (FLAGS / 'TESTED').touch()
        edited = FLAGS / 'EDITED'
        if edited.exists():
            edited.unlink()
    sys.exit(0)


if __name__ == '__main__':
    main()
