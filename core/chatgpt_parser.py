"""ChatGPT (OpenAI) export parser for Constellation.

Parses OpenAI's tree-structured conversations.json export into the same
normalized format that core/parser.py produces for Claude exports.

OpenAI uses a DAG/tree structure with a `mapping` field containing message
nodes linked by parent/children references. The active conversation thread
is traced backward from `current_node` to root.
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone


def epoch_to_iso(ts) -> str:
    """Convert Unix epoch float to ISO 8601 string."""
    if ts is None or ts == 0:
        return ''
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return ''


def normalize_title(title) -> str:
    """Normalize conversation title, replacing empty/default values with 'Untitled'."""
    if not title or not str(title).strip() or str(title).strip() == 'New chat':
        return 'Untitled'
    return str(title).strip()


def should_include_message(msg: dict) -> bool:
    """Determine if a message node should be included in the output."""
    if msg is None:
        return False

    role = msg.get('author', {}).get('role', '')
    if role in ('system', 'tool'):
        return False

    content = msg.get('content', {})
    if content.get('content_type') != 'text':
        return False

    parts = content.get('parts', [])
    if not parts:
        return False

    if not isinstance(parts[0], str):
        return False

    if not parts[0].strip():
        return False

    if msg.get('weight', 1.0) == 0:
        return False

    return True


def extract_text_from_parts(parts: list) -> str:
    """Extract text from content.parts, handling mixed content."""
    text_parts = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
    return ' '.join(text_parts).strip()


def linearize_conversation(conversation: dict) -> list:
    """Extract the active message thread by walking backward from current_node."""
    mapping = conversation.get('mapping', {})
    current_node_id = conversation.get('current_node')

    if not current_node_id or not mapping:
        return []

    messages_reversed = []
    node_id = current_node_id
    visited = set()

    while node_id and node_id in mapping:
        if node_id in visited:
            break  # circular reference protection
        visited.add(node_id)

        node = mapping[node_id]
        msg = node.get('message')

        if msg and should_include_message(msg):
            role_raw = msg.get('author', {}).get('role', '')
            role = 'user' if role_raw == 'user' else 'assistant'

            text = extract_text_from_parts(msg.get('content', {}).get('parts', []))
            timestamp = epoch_to_iso(msg.get('create_time'))

            messages_reversed.append({
                'role': role,
                'text': text,
                'timestamp': timestamp,
            })

        node_id = node.get('parent')

    messages_reversed.reverse()
    return messages_reversed


def resolve_export_files(path: str) -> list:
    """Resolve a ChatGPT export path to the list of conversation JSON files.

    Modern OpenAI exports shard conversations across ``conversations-000.json``
    ... ``conversations-NNN.json`` instead of a single ``conversations.json``.
    This resolver accepts, in order of preference:

    1. A directory  -> all ``conversations-*.json`` shards (sorted); falls back
       to a single ``conversations.json`` if no shards are present.
    2. A glob pattern (contains ``*``) -> its sorted matches.
    3. A single file -> itself. If it is named ``conversations.json`` and
       sibling ``conversations-*.json`` shards exist, the shards are used
       instead (the single file is the legacy name; shards are authoritative).

    Sorting is lexical, which orders ``conversations-000`` .. ``-012`` correctly.
    """
    if os.path.isdir(path):
        shards = sorted(glob.glob(os.path.join(path, 'conversations-*.json')))
        if shards:
            return shards
        single = os.path.join(path, 'conversations.json')
        return [single] if os.path.isfile(single) else []

    if '*' in path:
        return sorted(glob.glob(path))

    # Single file. Prefer sibling shards if present (legacy-name compatibility).
    parent = os.path.dirname(path) or '.'
    if os.path.basename(path) == 'conversations.json':
        shards = sorted(glob.glob(os.path.join(parent, 'conversations-*.json')))
        if shards:
            return shards
    return [path]


def parse_chatgpt_export(filepath: str, max_conversations: int = None) -> list:
    """Parse an OpenAI ChatGPT export (single file, sharded, directory, or glob).

    Traverses the DAG/tree structure via current_node -> root backward walk.
    Returns same format as parse_claude_export() for unified indexing.

    Args:
        filepath: Path to a ChatGPT export. May be a single ``conversations.json``
            file, a directory containing ``conversations-*.json`` shards, a glob
            pattern, or a legacy single-file path with sibling shards.
        max_conversations: Optional limit for testing/development (applied across
            all shards combined).

    Returns:
        List of conversation dicts with id, name, created_at, provider,
        messages, and user_messages fields.
    """
    files = resolve_export_files(filepath)
    if not files:
        print(f"No ChatGPT conversation files found at: {filepath}", file=sys.stderr)
        return []

    if len(files) == 1:
        print(f"Parsing ChatGPT export: {files[0]}", file=sys.stderr)
    else:
        print(f"Parsing ChatGPT export: {len(files)} shards under {filepath}",
              file=sys.stderr)

    conversations = []
    seen_ids = set()
    skipped = 0

    for fp in files:
        with open(fp, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        for conv in raw:
            if max_conversations and len(conversations) >= max_conversations:
                break

            conv_id = conv.get('id', '')
            if not conv_id:
                skipped += 1
                continue

            # Guard against the same conversation appearing in multiple shards.
            if conv_id in seen_ids:
                continue

            title = normalize_title(conv.get('title'))
            created_at = epoch_to_iso(conv.get('create_time'))

            messages = linearize_conversation(conv)
            if not messages:
                skipped += 1
                continue

            # Check if there are any user or assistant messages with content
            has_content = any(m['text'].strip() for m in messages)
            if not has_content:
                skipped += 1
                continue

            seen_ids.add(conv_id)
            user_messages = [m['text'] for m in messages if m['role'] == 'user']

            conversations.append({
                'id': conv_id,
                'name': title,
                'created_at': created_at,
                'provider': 'chatgpt',
                'messages': messages,
                'user_messages': user_messages,
            })

        if max_conversations and len(conversations) >= max_conversations:
            break

    total_messages = sum(len(c['messages']) for c in conversations)
    print(f"Parsed {len(conversations):,} ChatGPT conversations with "
          f"{total_messages:,} messages from {len(files)} file(s) "
          f"(skipped {skipped} empty)", file=sys.stderr)
    return conversations
