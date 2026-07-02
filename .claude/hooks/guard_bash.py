#!/usr/bin/env python3
"""PreToolUse:Bash guard (Phase 0, Group 3).

Reads the hook event JSON on stdin, tokenizes tool_input.command on shell
separators, and evaluates COMMAND-POSITION tokens only (so a commit message
containing the word 'python' never false-positives).

Exit 0 = allow.  Exit 2 = block, stderr is fed back to Claude as the remedy.
Exit 1 is never used for enforcement — it does not block.

stdlib only (sys, json, re, pathlib, os); runtime well under 200ms.
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get('CLAUDE_PROJECT_DIR',
                                  Path(__file__).resolve().parent.parent.parent))
MERGE_SAFE_FLAG = PROJECT_DIR / '.claude' / 'flags' / 'MERGE_SAFE'

# Shell separators that start a new command position. Parentheses/braces and
# command substitution openers also put the next token in command position.
SEPARATOR_RE = re.compile(r'(?:;|&&|\|\||\||\n|&|\$\(|`|\(|\{)')
# Words that prefix a command without being the command itself.
COMMAND_PREFIXES = {
    'sudo', 'env', 'nohup', 'time', 'exec', 'command', 'builtin',
    'xargs', 'caffeinate', 'nice', 'stdbuf',
}
BARE_PYTHON_RE = re.compile(r'^(python|python3|python3\.\d+|pip|pip3)$')
PROTECTED_RM_RE = re.compile(r'(data/notes\.json|(?:^|[\s"\'=])backups?/|(?:^|[\s"\'=])data/?(?:\s|$|"|\'))')


def block(reason: str):
    print(reason, file=sys.stderr)
    sys.exit(2)


def command_tokens(command: str):
    """Yield (command_word, full_segment) for each command position."""
    for segment in SEPARATOR_RE.split(command):
        words = segment.strip().split()
        # skip leading VAR=value assignments and known prefixes
        while words and ('=' in words[0] and not words[0].startswith(('-', '/'))
                         or words[0] in COMMAND_PREFIXES):
            words = words[1:]
        if words:
            yield words[0], segment.strip()


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # malformed event: never brick the session
    command = (event.get('tool_input') or {}).get('command') or ''
    if not command:
        sys.exit(0)

    # Rule 4: forbidden flag — anywhere in the command string.
    if '--dangerously-skip-permissions' in command:
        block('Forbidden flag. Use --permission-mode dontAsk with an allowlist.')

    # Rule 5: re-embed lock — anywhere, unless the MERGE_SAFE flag exists.
    if re.search(r'(?:--)?\breembed\b', command) and not MERGE_SAFE_FLAG.exists():
        block('Re-embed is LOCKED until the notes-merge regression suite passes '
              '(Group 4 creates the flag via `make merge-safe`). This lock '
              'prevented the March data loss from recurring.')

    for word, segment in command_tokens(command):
        base = word.rsplit('/', 1)[-1] if '/' in word else word

        # Rule 1: python/pip outside the venv, bare or by absolute path
        # (uv run / uv pip are fine — their command word is 'uv').
        if BARE_PYTHON_RE.match(base) and '.venv/bin/' not in word:
            block('Use .venv/bin/python — system Python is 3.13 and will '
                  'corrupt the environment.')

        # Rule 2: rm touching notes, data/, or backups/.
        if base == 'rm' and PROTECTED_RM_RE.search(segment):
            block('Notes and backups are protected. If truly required, Mike '
                  'removes them by hand.')

        if base == 'git':
            args = segment.split()
            # Rule 3: force pushes and hard resets.
            if 'push' in args and ('--force' in args or '-f' in args
                                   or '--force-with-lease' in args):
                block('Force operations forbidden. Stay on main, plain pushes only.')
            if 'reset' in args and '--hard' in args:
                block('Force operations forbidden. Stay on main, plain pushes only.')
            # Rule 6: branch creation.
            if ('checkout' in args and '-b' in args) or ('switch' in args and '-c' in args):
                block('Main-only workflow. If a branch is genuinely needed, '
                      'ask Mike explicitly.')

    sys.exit(0)


if __name__ == '__main__':
    main()
