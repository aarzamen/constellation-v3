"""Google AI Studio prompt parser (Phase 2 / D5).

AI Studio exports each saved prompt as an extensionless JSON file with
`runSettings` (incl. `model`), optional `systemInstruction`, and
`chunkedPrompt.chunks` (role-tagged user/model turns). This is the one source
with **native per-conversation model identity**, which is carried into the record.

Only chat-type prompts (>=1 user AND >=1 model turn) become conversations;
single-prompt drafts are skipped. provider: `aistudio`. Roles preserved
(user / model->assistant); thought chunks are dropped.
"""

import hashlib
import json
import os


def _model(d: dict) -> str:
    return (d.get('runSettings') or {}).get('model', '') or ''


def _system_instruction(d: dict) -> str:
    si = d.get('systemInstruction')
    if isinstance(si, dict):
        # may be {"text": ...} or {"parts":[{"text":...}]}
        if si.get('text'):
            return si['text'].strip()
        parts = si.get('parts') or []
        return ' '.join(p.get('text', '') for p in parts if isinstance(p, dict)).strip()
    return ''


def parse_prompt_file(path: str):
    """Parse one AI Studio prompt file into a conversation dict, or None if it is
    not a chat-type prompt (missing a user or model turn)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            d = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    if not isinstance(d, dict) or 'chunkedPrompt' not in d:
        return None

    chunks = (d.get('chunkedPrompt') or {}).get('chunks') or []
    messages, user_messages = [], []
    for ch in chunks:
        if ch.get('isThought'):
            continue
        text = (ch.get('text') or '').strip()
        if not text:
            continue
        role = ch.get('role')
        if role == 'user':
            messages.append({'role': 'user', 'text': text, 'timestamp': ''})
            user_messages.append(text)
        elif role == 'model':
            messages.append({'role': 'assistant', 'text': text, 'timestamp': ''})

    has_user = any(m['role'] == 'user' for m in messages)
    has_model = any(m['role'] == 'assistant' for m in messages)
    if not (has_user and has_model):
        return None  # non-chat / single-prompt draft

    stem = os.path.splitext(os.path.basename(path))[0]
    sid = hashlib.sha1(stem.encode('utf-8')).hexdigest()[:12]
    return {
        'id': f'aistudio_{sid}',
        'name': stem,
        'created_at': '',                 # no authored timestamp in the export
        'provider': 'aistudio',
        'model': _model(d),               # native per-conversation model identity
        'system_instruction': _system_instruction(d),
        'messages': messages,
        'user_messages': user_messages,
    }


def parse_aistudio_export(path: str) -> list:
    """Parse an AI Studio prompt file, or walk a directory of them. Chat-type
    only; deduped by id."""
    found = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in sorted(files):
                conv = parse_prompt_file(os.path.join(root, fn))
                if conv:
                    found.append(conv)
    elif os.path.isfile(path):
        conv = parse_prompt_file(path)
        if conv:
            found.append(conv)
    dedup = {}
    for c in found:
        dedup.setdefault(c['id'], c)
    return list(dedup.values())
