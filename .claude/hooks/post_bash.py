#!/usr/bin/env python3
"""PostToolUse:Bash hook (Phase 0, Group 3) — TESTED/EDITED flag wiring.

Design choice (spec left it open): the TESTED flag is maintained here rather
than in guard_bash.py because only PostToolUse sees the command's OUTPUT —
a PreToolUse hook cannot know whether pytest actually passed.

When a Bash command ran pytest and its output shows passing tests with no
failures/errors: touch .claude/flags/TESTED and remove .claude/flags/EDITED.
Always exits 0 — this hook observes, it never blocks.

Pass detection is on the OUTCOME COUNTS (`N passed`, and no `N failed` /
`N error`), NOT on pytest's `==== ... ====` banner. The banner is stripped
when output is piped through `tail`/`grep`/`head` or run under `-q` with no
warnings summary (real captured event: stdout ended `33 passed in 0.54s`
with no `=` decoration), which silently defeated the old regex and left
stop_discipline blocking a clean, tested tree. Counts survive all of that.
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get('CLAUDE_PROJECT_DIR',
                                  Path(__file__).resolve().parent.parent.parent))
FLAGS = PROJECT_DIR / '.claude' / 'flags'

# "33 passed", "166 passed, 4 warnings", "==== 166 passed ====" all match.
PASSED_RE = re.compile(r'\b(\d+) passed\b')
# Real failures: "1 failed", "1 error", "2 errors". Does NOT match "1 xfailed"
# (that token is "xfailed", not " failed") or "0 selected".
FAILED_RE = re.compile(r'\b(\d+) (?:failed|error)')


def ran_pytest_clean(command: str, output: str) -> bool:
    """True iff `command` invoked pytest and `output` shows a clean pass."""
    if 'pytest' not in command:
        return False
    return bool(PASSED_RE.search(output)) and not FAILED_RE.search(output)


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = (event.get('tool_input') or {}).get('command') or ''
    response = event.get('tool_response') or {}
    if isinstance(response, dict):
        output = (response.get('stdout') or '') + (response.get('stderr') or '')
    else:
        output = str(response)

    if ran_pytest_clean(command, output):
        FLAGS.mkdir(parents=True, exist_ok=True)
        (FLAGS / 'TESTED').touch()
        edited = FLAGS / 'EDITED'
        if edited.exists():
            edited.unlink()
    sys.exit(0)


if __name__ == '__main__':
    main()
