"""Abacus.AI (ChatLLM Teams) export parser for Constellation.

Abacus's `export_chat_history.py` tool emits a single JSON object:

    {
      "export_metadata": {...},
      "deployed_applications": [...],
      "artifact_manifest": [...],
      "conversations": [
        {
          "uuid": "...", "conversation_id": "...", "name": "...",
          "created_at": "...", "chat_messages": [
            {"sender": "human"|"assistant", "role": ..., "text": "...",
             "created_at": "...", "segments": [...]}
          ]
        }, ...
      ]
    }

We flatten `conversations[]` into the unified Constellation schema. provider tag:
`abacus`. Conversations with no text-bearing messages are skipped.
"""

import json
import sys


def _norm_role(sender: str) -> str:
    return 'user' if sender in ('human', 'user') else 'assistant'


def parse_abacus_export(filepath: str) -> list:
    """Parse an Abacus ChatLLM export JSON into conversation dicts with
    id, name, created_at, provider, messages, user_messages."""
    print(f"Parsing Abacus export: {filepath}", file=sys.stderr)
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    convs = raw.get('conversations', raw) if isinstance(raw, dict) else raw

    conversations = []
    for conv in convs:
        conv_id = conv.get('conversation_id') or conv.get('uuid')
        if not conv_id:
            continue
        name = conv.get('name') or 'Abacus chat'
        created_at = conv.get('created_at', '')

        messages, user_messages = [], []
        for m in conv.get('chat_messages', []) or []:
            sender = m.get('sender', m.get('role', ''))
            role = _norm_role(sender)
            text = m.get('text', '')
            if isinstance(text, list):
                text = ' '.join(str(p) for p in text if isinstance(p, str))
            if not text or not str(text).strip():
                continue
            text = str(text).strip()
            messages.append({
                'role': role,
                'text': text,
                'timestamp': m.get('created_at', ''),
            })
            if role == 'user':
                user_messages.append(text)

        if not messages:
            continue

        conversations.append({
            'id': f'abacus_{conv_id}',
            'name': name,
            'created_at': created_at,
            'provider': 'abacus',
            'messages': messages,
            'user_messages': user_messages,
        })

    print(f"Parsed {len(conversations)} Abacus conversations with "
          f"{sum(len(c['messages']) for c in conversations)} messages",
          file=sys.stderr)
    return conversations
