#!/usr/bin/env python3
"""PreToolUse:Read|Edit|Write guard (Phase 0, Group 3).

Blocks (exit 2):
  * .env at any depth               — any tool  (secrets never enter context)
  * backups/ at any depth           — any tool  (snapshots are hands-off)
  * data/notes.json                 — Edit|Write only (the server and pipeline
                                      mutate notes; a raw agent edit is always
                                      a mistake — Read is fine for probing)

Exit 0 = allow. Exit 2 = block with the reason on stderr. stdlib only.
"""

import json
import re
import sys


def block(reason: str):
    print(reason, file=sys.stderr)
    sys.exit(2)


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool = event.get('tool_name') or ''
    path = (event.get('tool_input') or {}).get('file_path') or ''
    if not path:
        sys.exit(0)

    norm = path.replace('\\', '/')
    name = norm.rsplit('/', 1)[-1]

    if name == '.env' or name.startswith('.env.'):
        block('.env is protected — secrets are checked by presence only '
              '(scripts/preflight.sh), never read into context.')

    if re.search(r'(^|/)backups(/|$)', norm):
        block('backups/ is protected. Snapshots are restored by Mike by hand, '
              'never touched by the agent.')

    if tool in ('Edit', 'Write') and norm.endswith('data/notes.json'):
        block('data/notes.json is mutated only by the MCP server and the '
              'pipeline. Use add_conversation_note/delete_conversation_note '
              'instead of editing the sidecar raw.')

    sys.exit(0)


if __name__ == '__main__':
    main()
