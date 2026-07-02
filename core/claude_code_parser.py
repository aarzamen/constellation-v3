"""Claude Code session JSONL parser (Phase 2).

Parses `~/.claude/projects/<project>/<session>.jsonl` transcripts into the
unified conversation format with provider `claude-code`. One JSONL file == one
conversation; id == `local_<session-uuid>` (the filename stem), matching the
shape of the claude-code conversations already in the corpus.

Extraction rules (kept deliberately narrow so re-parsing is deterministic):
  * Only `user` / `assistant` records contribute messages.
  * Message text is the concatenation of `text` content blocks only — thinking
    blocks, tool_use, and tool_result are dropped (reused via
    core.parser.extract_text_from_content).
  * `isMeta` / `isSidechain` records are skipped (system/meta + subagent noise).
  * Name comes from the last `ai-title` record's `aiTitle`, else a snippet of
    the first user message.
  * created_at is the earliest message timestamp.
Records with no extractable text produce no conversation (returns None).
"""

import json
import os

from core.parser import extract_text_from_content


def _iter_records(path):
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def parse_session_file(path: str):
    """Parse a single session JSONL into a conversation dict, or None if the
    file holds no user/assistant text. Deterministic (idempotent)."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    messages = []
    user_messages = []
    title = ''

    for rec in _iter_records(path):
        rtype = rec.get('type')
        if rtype == 'ai-title':
            title = rec.get('aiTitle') or rec.get('title') or title
            continue
        if rtype not in ('user', 'assistant'):
            continue
        if rec.get('isMeta') or rec.get('isSidechain'):
            continue

        msg = rec.get('message') or {}
        text = (extract_text_from_content(msg.get('content', '')) or '').strip()
        if not text:
            continue

        role = 'user' if rtype == 'user' else 'assistant'
        messages.append({
            'role': role,
            'text': text,
            'timestamp': rec.get('timestamp', '') or '',
        })
        if role == 'user':
            user_messages.append(text)

    if not messages:
        return None

    created_at = min((m['timestamp'] for m in messages if m['timestamp']),
                     default='')
    if not title:
        first = next((m['text'] for m in messages if m['role'] == 'user'),
                     messages[0]['text'])
        title = (first[:60] + '…') if len(first) > 60 else first

    return {
        'id': f'local_{session_id}',
        'name': title or 'Claude Code Session',
        'created_at': created_at,
        'provider': 'claude-code',
        'messages': messages,
        'user_messages': user_messages,
    }


def parse_claude_code_export(path: str) -> list:
    """Parse a session .jsonl file, or walk a directory tree of them.

    Returns a list of conversation dicts (possibly empty). Registered in
    core.provider_registry as provider 'claude-code'.
    """
    conversations = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in sorted(files):
                if fn.endswith('.jsonl'):
                    conv = parse_session_file(os.path.join(root, fn))
                    if conv:
                        conversations.append(conv)
    elif path.endswith('.jsonl') and os.path.isfile(path):
        conv = parse_session_file(path)
        if conv:
            conversations.append(conv)
    return conversations
