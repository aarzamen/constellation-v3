"""Gemini AI Studio export parser for Constellation.

Parses individual Gemini chat session files from the AI Studio Google Drive
folder. Unlike Claude/ChatGPT which export a single conversations.json,
Gemini stores each conversation as a separate extensionless JSON file.

The parser takes a DIRECTORY path (not a single file) and scans for valid
Gemini chat JSON files, skipping non-JSON files (images, videos, PDFs, code).
"""

import hashlib
import json
import os
import sys


# Extensions that are definitely NOT Gemini chat files
NON_CHAT_EXTENSIONS = {
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico',
    '.mp4', '.mov', '.avi', '.wav', '.mp3', '.ogg', '.flac',
    '.dmg', '.zip', '.tar', '.gz', '.rar', '.7z',
    '.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.html', '.htm',
    '.json', '.md', '.txt', '.rtf', '.docx', '.doc', '.xlsx', '.csv',
    '.c', '.cpp', '.h', '.java', '.go', '.rs', '.rb', '.php',
    '.yaml', '.yml', '.toml', '.xml', '.ini', '.cfg',
    '.sh', '.bash', '.zsh', '.bat', '.ps1',
    '.exe', '.dll', '.so', '.dylib',
    '.pem', '.key', '.crt', '.cer',
    '.sqlite', '.db',
}


def is_gemini_chat_file(filepath):
    """Determine if a file is a Gemini chat session (not an attachment)."""
    _, ext = os.path.splitext(filepath)
    if ext.lower() in NON_CHAT_EXTENSIONS:
        return False

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            header = f.read(2000)
            return '"chunkedPrompt"' in header or '"runSettings"' in header
    except (UnicodeDecodeError, IOError):
        return False


def generate_gemini_id(filepath):
    """Generate a stable UUID-like ID from filename.

    Since Gemini files don't have UUIDs, we hash the filename for stability.
    """
    filename = os.path.basename(filepath)
    h = hashlib.sha256(filename.encode()).hexdigest()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}'


def extract_gemini_messages(data):
    """Extract messages from chunkedPrompt.chunks."""
    chunks = data.get('chunkedPrompt', {}).get('chunks', [])
    messages = []

    for chunk in chunks:
        role = chunk.get('role', '')
        if role == 'user':
            role = 'user'
        elif role == 'model':
            role = 'assistant'
        else:
            continue

        # Skip thinking blocks
        if chunk.get('isThought', False):
            continue

        # Extract text from parts
        parts = chunk.get('parts', [])
        text_parts = []
        for part in parts:
            if isinstance(part, dict) and 'text' in part:
                text_parts.append(part['text'])

        text = ' '.join(text_parts).strip()
        if not text:
            continue

        messages.append({
            'role': role,
            'text': text,
            'timestamp': '',  # Gemini has no per-message timestamps
        })

    return messages


def parse_gemini_export(directory_path):
    """Parse a folder of Gemini AI Studio chat files.

    Args:
        directory_path: Path to the Google AI Studio folder.

    Returns:
        List of conversation dicts in the standard Constellation format.
    """
    print(f"Scanning Gemini AI Studio folder: {directory_path}", file=sys.stderr)

    if not os.path.isdir(directory_path):
        print(f"Error: Not a directory: {directory_path}", file=sys.stderr)
        return []

    conversations = []
    skipped = 0
    scanned = 0

    for entry in os.listdir(directory_path):
        filepath = os.path.join(directory_path, entry)
        if not os.path.isfile(filepath):
            continue

        scanned += 1

        if not is_gemini_chat_file(filepath):
            skipped += 1
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped += 1
            continue

        messages = extract_gemini_messages(data)
        if not messages:
            skipped += 1
            continue

        # Use filename as title
        title = entry
        if len(title) > 100:
            title = title[:97] + '...'
        if not title.strip():
            title = 'Untitled'

        # Try to get timestamp from file modification time
        try:
            from datetime import datetime, timezone
            mtime = os.path.getmtime(filepath)
            created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except (OSError, ValueError):
            created_at = ''

        user_messages = [m['text'] for m in messages if m['role'] == 'user']

        conversations.append({
            'id': generate_gemini_id(filepath),
            'name': title,
            'created_at': created_at,
            'provider': 'gemini',
            'messages': messages,
            'user_messages': user_messages,
        })

    print(f"Parsed {len(conversations):,} Gemini conversations "
          f"(skipped {skipped} non-chat files, scanned {scanned} total)",
          file=sys.stderr)
    return conversations
