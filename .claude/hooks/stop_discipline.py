#!/usr/bin/env python3
"""Stop hook (Phase 0, Group 3) — the deferral killer.

Order of checks (first hit blocks with exit 2):
  0. stop_hook_active true  -> exit 0 immediately (prevents infinite loop)
  1. uncommitted work       -> commit before stopping (audit-trail rule)
  2. untested edits         -> EDITED flag newer than TESTED flag
  3. deferral language      -> scan the last assistant message for handback
                               phrases; lines mentioning MANUAL_STEPS.md are
                               whitelisted so legitimately logged manual steps
                               don't re-trigger the block.

Exit 0 = allow stop. Exit 2 = block, stderr fed back to Claude. stdlib only.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get('CLAUDE_PROJECT_DIR',
                                  Path(__file__).resolve().parent.parent.parent))
FLAGS = PROJECT_DIR / '.claude' / 'flags'

DEFERRAL_PATTERNS = [
    r'\byou can manually\b',
    r"\byou'll need to\b",
    r'\byou will need to\b',
    r'\bplease run\b',
    r'\byou should run\b',
    r'\bmanually run\b',
    r'\bleft as an exercise\b',
    r'\bfor now[, ]',
    r'\bplaceholder\b',
    r'\bskipping (this|that|for now)\b',
    r'\bwhen you get a chance\b',
    r'\bin a future (sprint|session)\b',
    r'\boutside the scope\b',
]
DEFERRAL_RE = re.compile('|'.join(DEFERRAL_PATTERNS), re.IGNORECASE)


def block(reason: str):
    print(reason, file=sys.stderr)
    sys.exit(2)


def last_assistant_message(event: dict) -> str:
    msg = event.get('last_assistant_message')
    if msg:
        return msg
    # Fallback: tail the transcript JSONL for the last assistant text blocks.
    tp = event.get('transcript_path')
    if not tp or not os.path.exists(tp):
        return ''
    try:
        lines = Path(tp).read_text(errors='replace').splitlines()
    except OSError:
        return ''
    for line in reversed(lines[-200:]):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get('type') != 'assistant':
            continue
        content = (entry.get('message') or {}).get('content') or []
        texts = [b.get('text', '') for b in content
                 if isinstance(b, dict) and b.get('type') == 'text']
        if texts:
            return '\n'.join(texts)
    return ''


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # 0. Never loop.
    if event.get('stop_hook_active'):
        sys.exit(0)

    # 1. Uncommitted work.
    try:
        porcelain = subprocess.run(
            ['git', 'status', '--porcelain'], cwd=str(PROJECT_DIR),
            capture_output=True, text=True, timeout=10).stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        porcelain = ''
    if porcelain:
        block('Uncommitted changes present. Commit with a conventional message '
              'before stopping (audit-trail rule — the June report found zero '
              'logged commits across 11 sessions).')

    # 2. Untested edits.
    edited, tested = FLAGS / 'EDITED', FLAGS / 'TESTED'
    if edited.exists() and (not tested.exists()
                            or edited.stat().st_mtime > tested.stat().st_mtime):
        block("Code was edited but pytest hasn't run since. Run the suite "
              '(.venv/bin/python -m pytest tests/), then stop.')

    # 3. Deferral language (skip lines that log to MANUAL_STEPS.md).
    message = last_assistant_message(event)
    for line in message.splitlines():
        if 'MANUAL_STEPS.md' in line:
            continue
        m = DEFERRAL_RE.search(line)
        if m:
            block(f"Deferral detected: '{m.group(0)}'. Execute it now, or "
                  'record it in MANUAL_STEPS.md with exact commands and a '
                  'one-line justification, then stop.')

    sys.exit(0)


if __name__ == '__main__':
    main()
