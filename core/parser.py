"""Claude export JSON parser for Constellation.

Parses Claude's conversations.json export into a unified format.
Supports Claude, ChatGPT, Gemini, and Grok exports.
"""

import json
import os
import re
import sys
import zipfile
from datetime import datetime


# Stopwords for term extraction (from V1/V2)
STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'it', 'this', 'that', 'was', 'are',
    'be', 'has', 'had', 'have', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'can', 'not', 'no', 'so', 'if',
    'then', 'than', 'too', 'very', 'just', 'about', 'up', 'out', 'how',
    'what', 'when', 'where', 'who', 'which', 'why', 'all', 'each', 'every',
    'both', 'few', 'more', 'most', 'other', 'some', 'such', 'only', 'own',
    'same', 'also', 'into', 'over', 'after', 'before', 'between', 'under',
    'again', 'once', 'here', 'there', 'any', 'your', 'my', 'his', 'her',
    'its', 'our', 'their', 'me', 'him', 'them', 'we', 'you', 'he', 'she',
    'they', 'i', 'been', 'being', 'were', 'am', 'as', 'well', 'like',
    'even', 'still', 'now', 'get', 'got', 'let', 'make', 'made', 'want',
    'need', 'use', 'used', 'using', 'know', 'think', 'see', 'way', 'go',
    'going', 'one', 'two', 'new', 'first', 'last', 'long', 'great', 'much',
    'sure', 'yes', 'okay', 'ok', 'thanks', 'thank', 'please', 'help',
    'would', 'could', 'should', 'might', 'right', 'good', 'really',
    'don', 'doesn', 'didn', 'won', 'can', 'something', 'things', 'thing',
    'work', 'working', 'code', 'file', 'example', 'look', 'take',
}


def extract_message_timestamp(msg: dict) -> str:
    """Extract the best available timestamp from an Anthropic message object.

    Priority:
    1. msg['created_at'] — message-level timestamp (microsecond ISO 8601)
    2. msg['content'][0]['start_timestamp'] — content block timestamp
    3. msg['timestamp'] — fallback field name
    4. '' — empty string if nothing found
    """
    ts = msg.get('created_at', '')
    if ts:
        return ts

    content = msg.get('content', [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                ts = item.get('start_timestamp', '')
                if ts:
                    return ts

    return msg.get('timestamp', '')


def extract_text_from_content(content) -> str:
    """Extract plain text from Claude's content field (handles both string and list formats)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                if item.get('type') == 'text':
                    texts.append(item.get('text', ''))
        return ' '.join(texts)
    return ''


def chunk_text(text: str, max_words: int = 150) -> list:
    """Recursively chunk text into segments of roughly max_words."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    
    # Try to split by double newline (paragraphs)
    paragraphs = text.split('\n\n')
    if len(paragraphs) > 1:
        chunks = []
        current_chunk = []
        current_words = 0
        for p in paragraphs:
            p_words = len(p.split())
            if current_words + p_words > max_words and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [p]
                current_words = p_words
            else:
                current_chunk.append(p)
                current_words += p_words
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        # Now check if any chunk is STILL too big, and split by words as fallback
        final_chunks = []
        for c in chunks:
            if len(c.split()) > max_words:
                c_words = c.split()
                for i in range(0, len(c_words), max_words):
                    final_chunks.append(' '.join(c_words[i:i+max_words]))
            else:
                final_chunks.append(c)
        return final_chunks
    
    # Fallback to word splitting
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(' '.join(words[i:i+max_words]))
    return chunks


def parse_claude_export(filepath: str) -> list:
    """Parse Claude's conversations.json export.

    Returns list of conversation dicts with:
        id, name, created_at, provider, messages, user_messages
    """
    print(f"Parsing {filepath}...", file=sys.stderr)

    with open(filepath, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    conversations = []
    for conv in raw:
        conv_id = conv.get('uuid', conv.get('id', ''))
        name = conv.get('name', conv.get('title', 'Untitled'))
        created_at = conv.get('created_at', conv.get('create_time', ''))

        # Parse messages from chat_messages
        messages = []
        user_messages = []
        raw_messages = conv.get('chat_messages', conv.get('messages', []))

        if raw_messages is None:
            raw_messages = []

        for msg in raw_messages:
            sender = msg.get('sender', msg.get('role', ''))
            role = 'user' if sender in ('human', 'user') else 'assistant'
            text = extract_text_from_content(msg.get('text', msg.get('content', '')))

            if not text or not text.strip():
                continue

            timestamp = extract_message_timestamp(msg)
            messages.append({
                'role': role,
                'text': text.strip(),
                'timestamp': timestamp,
            })

            if role == 'user':
                user_messages.append(text.strip())

        if not messages:
            continue

        conversations.append({
            'id': conv_id,
            'name': name or 'Untitled',
            'created_at': created_at,
            'provider': 'claude',
            'messages': messages,
            'user_messages': user_messages,
        })

    print(f"Parsed {len(conversations)} conversations with "
          f"{sum(len(c['messages']) for c in conversations)} messages", file=sys.stderr)
    return conversations


def extract_top_terms(text: str, n: int = 5) -> list:
    """Extract top N meaningful terms from text."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    word_counts = {}
    for w in words:
        if w not in STOPWORDS and len(w) > 2:
            word_counts[w] = word_counts.get(w, 0) + 1
    sorted_words = sorted(word_counts.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:n]]


CLAUDE_EXPORT_PATTERN = re.compile(
    r'data-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-batch-\d{4}\.zip$'
)


def find_claude_export(search_dir: str = None) -> list:
    """Scan for Claude export files in ~/Downloads.

    Finds:
    1. Zip files matching data-YYYY-MM-DD-HH-MM-SS-batch-NNNN.zip
    2. Directories containing conversations.json
    3. Direct conversations.json files
    """
    if search_dir is None:
        search_dir = os.path.expanduser('~/Downloads')

    found = []
    if not os.path.isdir(search_dir):
        return found

    for entry in os.listdir(search_dir):
        full_path = os.path.join(search_dir, entry)

        # Check for Claude export zip files
        if CLAUDE_EXPORT_PATTERN.match(entry) and os.path.isfile(full_path):
            try:
                with zipfile.ZipFile(full_path) as zf:
                    if 'conversations.json' in zf.namelist():
                        found.append(('zip', full_path))
            except zipfile.BadZipFile:
                pass
            continue

        # Check for direct conversations.json
        if entry == 'conversations.json' and os.path.isfile(full_path):
            found.append(('json', full_path))
            continue

        # Check inside directories (Claude export folders)
        if os.path.isdir(full_path):
            conv_path = os.path.join(full_path, 'conversations.json')
            if os.path.isfile(conv_path):
                found.append(('json', conv_path))

    # Sort by modification time, newest first
    found.sort(key=lambda p: os.path.getmtime(p[1]), reverse=True)
    return found
