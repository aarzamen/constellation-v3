"""ChatGPT (OpenAI) export parser for Constellation.

Parses OpenAI's tree-structured conversations.json export into the same
normalized format that core/parser.py produces for Claude exports.

OpenAI uses a DAG/tree structure with a `mapping` field containing message
nodes linked by parent/children references. The active conversation thread
is traced backward from `current_node` to root.
"""

import json
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


def parse_chatgpt_export(filepath: str, max_conversations: int = None) -> list:
    """Parse OpenAI ChatGPT conversations.json export.

    Traverses the DAG/tree structure via current_node -> root backward walk.
    Returns same format as parse_claude_export() for unified indexing.

    Args:
        filepath: Path to ChatGPT conversations.json file.
        max_conversations: Optional limit for testing/development.

    Returns:
        List of conversation dicts with id, name, created_at, provider,
        messages, and user_messages fields.
    """
    print(f"Parsing ChatGPT export: {filepath}", file=sys.stderr)

    with open(filepath, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    conversations = []
    skipped = 0

    for conv in raw:
        if max_conversations and len(conversations) >= max_conversations:
            break

        conv_id = conv.get('id', '')
        if not conv_id:
            skipped += 1
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

        user_messages = [m['text'] for m in messages if m['role'] == 'user']

        conversations.append({
            'id': conv_id,
            'name': title,
            'created_at': created_at,
            'provider': 'chatgpt',
            'messages': messages,
            'user_messages': user_messages,
        })

    total_messages = sum(len(c['messages']) for c in conversations)
    print(f"Parsed {len(conversations):,} ChatGPT conversations with "
          f"{total_messages:,} messages (skipped {skipped} empty)",
          file=sys.stderr)
    return conversations
