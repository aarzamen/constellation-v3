"""Grok (xAI) export parser for Constellation.

STATUS: STUB — Grok's export format via accounts.x.ai/data is not fully
documented. This parser attempts auto-detection of the format.

Known facts:
- Export via accounts.x.ai/data -> ZIP file
- Contains conversation history in JSON format
- xAI API uses OpenAI-compatible format, so exports MAY be similar

The user has ~1M tokens of Grok conversation history.
"""

import json
import os
import sys
import zipfile


def _try_extract_json_from_zip(filepath):
    """Try to find and extract a conversations JSON file from a ZIP."""
    common_names = [
        'conversations.json', 'chats.json', 'data.json',
        'messages.json', 'history.json', 'export.json',
    ]

    try:
        with zipfile.ZipFile(filepath) as zf:
            names = zf.namelist()

            # Try common filenames
            for name in common_names:
                if name in names:
                    with zf.open(name) as f:
                        return json.load(f), name

            # Try any .json file
            json_files = [n for n in names if n.endswith('.json')]
            if json_files:
                with zf.open(json_files[0]) as f:
                    return json.load(f), json_files[0]

    except (zipfile.BadZipFile, json.JSONDecodeError) as e:
        print(f"Grok ZIP extraction failed: {e}", file=sys.stderr)

    return None, None


def parse_grok_export(filepath):
    """Parse Grok export. Auto-detects format.

    Args:
        filepath: Path to Grok export file (JSON or ZIP).

    Returns:
        List of conversation dicts in standard Constellation format.
    """
    print(f"Parsing Grok export: {filepath}", file=sys.stderr)

    data = None

    # Handle ZIP files
    if zipfile.is_zipfile(filepath):
        data, source_name = _try_extract_json_from_zip(filepath)
        if data is None:
            print(f"WARNING: Could not find conversation JSON in Grok ZIP", file=sys.stderr)
            return []
        print(f"Found {source_name} in ZIP", file=sys.stderr)
    else:
        # Try direct JSON parse
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"WARNING: Could not parse Grok export: {e}", file=sys.stderr)
            return []

    # Auto-detect format
    if isinstance(data, list) and len(data) > 0:
        sample = data[0]

        if isinstance(sample, dict) and 'mapping' in sample:
            # Looks like OpenAI/ChatGPT format
            print("Grok export appears to use ChatGPT-compatible format, "
                  "using ChatGPT parser", file=sys.stderr)
            from core.chatgpt_parser import parse_chatgpt_export
            # Write to temp file for the parser
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                              delete=False) as tmp:
                json.dump(data, tmp)
                tmp.flush()
                convs = parse_chatgpt_export(tmp.name)
            os.unlink(tmp.name)
            # Override provider field
            for c in convs:
                c['provider'] = 'grok'
            return convs

        if isinstance(sample, dict) and ('messages' in sample or 'chat_messages' in sample):
            # Flat message array — similar to Claude
            print("Grok export appears to use flat message format", file=sys.stderr)
            conversations = []
            for conv in data:
                conv_id = conv.get('id', conv.get('uuid', ''))
                if not conv_id:
                    continue
                title = conv.get('title', conv.get('name', 'Untitled'))
                created_at = conv.get('created_at', conv.get('create_time', ''))

                raw_msgs = conv.get('messages', conv.get('chat_messages', []))
                if not raw_msgs:
                    continue

                messages = []
                user_messages = []
                for msg in raw_msgs:
                    role_raw = msg.get('role', msg.get('sender', ''))
                    role = 'user' if role_raw in ('user', 'human') else 'assistant'
                    text = msg.get('text', msg.get('content', ''))
                    if isinstance(text, list):
                        text = ' '.join(str(p) for p in text if isinstance(p, str))
                    if not text or not str(text).strip():
                        continue
                    text = str(text).strip()
                    messages.append({
                        'role': role,
                        'text': text,
                        'timestamp': msg.get('created_at', msg.get('timestamp', '')),
                    })
                    if role == 'user':
                        user_messages.append(text)

                if not messages:
                    continue

                conversations.append({
                    'id': conv_id,
                    'name': title or 'Untitled',
                    'created_at': str(created_at),
                    'provider': 'grok',
                    'messages': messages,
                    'user_messages': user_messages,
                })
            print(f"Parsed {len(conversations)} Grok conversations", file=sys.stderr)
            return conversations

    # Unknown format
    sample_info = 'N/A'
    if isinstance(data, list) and data:
        sample_info = str(list(data[0].keys())) if isinstance(data[0], dict) else type(data[0]).__name__
    elif isinstance(data, dict):
        sample_info = str(list(data.keys()))

    print(f"WARNING: Could not auto-detect Grok export format. "
          f"File structure: {type(data).__name__}, "
          f"sample keys: {sample_info}", file=sys.stderr)
    return []
