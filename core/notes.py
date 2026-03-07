"""Sidecar note persistence for Constellation V3.

Notes are stored in data/notes.json, separate from conversations.json,
so they survive pipeline rebuilds (--reembed).
"""

import json
import os
import sys
import uuid
import datetime


def _notes_path(data_dir):
    return os.path.join(data_dir, 'notes.json')


def load_notes(data_dir):
    """Load notes from sidecar file. Returns empty dict if missing."""
    path = _notes_path(data_dir)
    if not os.path.exists(path):
        # One-time migration: check conversations.json for orphaned notes
        _migrate_legacy_notes(data_dir)
        # Re-check after migration
        if not os.path.exists(path):
            return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Warning: notes.json is corrupted, starting fresh: {e}", file=sys.stderr)
        return {}


def save_notes(data_dir, notes_dict):
    """Atomically write notes dict to sidecar file."""
    path = _notes_path(data_dir)
    tmp_path = path + '.tmp'
    os.makedirs(data_dir, exist_ok=True)
    with open(tmp_path, 'w') as f:
        json.dump(notes_dict, f, indent=2)
    os.replace(tmp_path, path)


def append_note(data_dir, conversation_id, text):
    """Add a note to a conversation. Returns the new note dict."""
    notes = load_notes(data_dir)
    if conversation_id not in notes:
        notes[conversation_id] = []

    note = {
        'text': text,
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'note_id': uuid.uuid4().hex[:8],
    }
    notes[conversation_id].append(note)
    save_notes(data_dir, notes)
    return note


def delete_note(data_dir, conversation_id, note_id):
    """Delete a note by note_id. Returns True if found and deleted."""
    notes = load_notes(data_dir)
    conv_notes = notes.get(conversation_id, [])
    for i, n in enumerate(conv_notes):
        if n.get('note_id') == note_id:
            conv_notes.pop(i)
            if not conv_notes:
                del notes[conversation_id]
            save_notes(data_dir, notes)
            return True
    return False


def get_notes_for_conversation(data_dir, conversation_id):
    """Get all notes for a conversation."""
    notes = load_notes(data_dir)
    return notes.get(conversation_id, [])


def _migrate_legacy_notes(data_dir):
    """One-time migration: pull notes from conversations.json into sidecar."""
    conv_path = os.path.join(data_dir, 'conversations.json')
    if not os.path.exists(conv_path):
        return

    try:
        with open(conv_path, 'r') as f:
            conversations = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return

    migrated = {}
    for conv in conversations:
        if 'notes' in conv and conv['notes']:
            conv_id = conv.get('id', '')
            migrated[conv_id] = []
            for note in conv['notes']:
                migrated[conv_id].append({
                    'text': note.get('text', ''),
                    'created_at': note.get('created_at', ''),
                    'note_id': uuid.uuid4().hex[:8],
                })

    if migrated:
        save_notes(data_dir, migrated)
        print(f"Migrated {sum(len(v) for v in migrated.values())} legacy notes to notes.json", file=sys.stderr)
