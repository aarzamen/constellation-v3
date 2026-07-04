"""NotebookLM chat-session parser (Phase 2 / D3).

Google Takeout exports NotebookLM per-notebook chat as HTML files at
`<notebook>/Chat History/Chat Session - <uuid>.html`, with turns delimited by
`USER:` / `MODEL:` line markers followed by HTML content. One session file = one
conversation; provider `notebooklm`.

USER/MODEL roles are preserved in the parsed messages (mapped user/assistant)
even though only user messages embed today.
"""

import html
import os
import re

_ROLE_RE = re.compile(r'^(USER|MODEL):[ \t]?', re.MULTILINE)


def _strip_html(s: str) -> str:
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def _session_uuid(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r'Chat Session\s*-\s*([0-9a-f-]+)', stem)
    return m.group(1) if m else re.sub(r'[^0-9a-zA-Z-]', '_', stem)


def _notebook_title(path: str) -> str:
    # .../<NotebookTitle>/Chat History/Chat Session - <uuid>.html
    chat_hist = os.path.dirname(os.path.abspath(path))
    return os.path.basename(os.path.dirname(chat_hist)) or 'NotebookLM Notebook'


def parse_session_html(path: str):
    """Parse one Chat Session HTML into a conversation dict, or None if empty."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read()

    parts = _ROLE_RE.split(raw)          # [preamble, role, content, role, content, ...]
    segs = parts[1:]
    messages, user_messages = [], []
    for i in range(0, len(segs) - 1, 2):
        role_tag, content = segs[i], segs[i + 1]
        text = _strip_html(content)
        if not text:
            continue
        role = 'user' if role_tag == 'USER' else 'assistant'
        messages.append({'role': role, 'text': text, 'timestamp': ''})
        if role == 'user':
            user_messages.append(text)

    if not messages:
        return None

    return {
        'id': f'notebooklm_{_session_uuid(path)}',
        'name': _notebook_title(path),
        'created_at': '',                 # per-message timestamps absent in the export
        'provider': 'notebooklm',
        'messages': messages,
        'user_messages': user_messages,
    }


def parse_notebooklm_export(path: str) -> list:
    """Parse a Chat Session .html file, or walk a tree of NotebookLM exports.
    Dedupes by conversation id (NotebookLM/NotebookLM2 overlap by notebook)."""
    found = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in sorted(files):
                if fn.startswith('Chat Session') and fn.endswith('.html'):
                    conv = parse_session_html(os.path.join(root, fn))
                    if conv:
                        found.append(conv)
    elif path.endswith('.html') and os.path.isfile(path):
        conv = parse_session_html(path)
        if conv:
            found.append(conv)
    dedup = {}
    for c in found:
        dedup.setdefault(c['id'], c)
    return list(dedup.values())
