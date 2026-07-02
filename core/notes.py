"""Sidecar note persistence for Constellation.

Notes are stored in data/notes.json, separate from conversations.json,
so they survive pipeline rebuilds (--reembed).
"""

import datetime
import glob
import json
import os
import shutil
import sys
import uuid


def _notes_path(data_dir):
    return os.path.join(data_dir, 'notes.json')


def _utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


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
        # Quarantine the corrupted file BEFORE returning {} — otherwise the
        # next save_notes() would silently overwrite whatever text survives
        # in it (this exact path is how the March data loss became total).
        quarantine = f'{path}.corrupt.{_utc_stamp()}'
        try:
            shutil.copy2(path, quarantine)
            print(f"Warning: notes.json is corrupted ({e}); "
                  f"quarantined a copy at {quarantine}", file=sys.stderr)
        except OSError:
            print(f"Warning: notes.json is corrupted and could not be "
                  f"quarantined: {e}", file=sys.stderr)
        return {}


def save_notes(data_dir, notes_dict):
    """Atomically write notes dict to sidecar file."""
    path = _notes_path(data_dir)
    tmp_path = path + '.tmp'
    os.makedirs(data_dir, exist_ok=True)
    with open(tmp_path, 'w') as f:
        json.dump(notes_dict, f, indent=2)
    os.replace(tmp_path, path)


def merge_notes(preserved, current):
    """Merge two sidecar dicts — union by note_id (Phase 0, Group 4).

    `preserved` is the snapshot taken before a pipeline rebuild; `current`
    is whatever is on disk after it (including notes added mid-rebuild).
    Collision on note_id: identical text -> dedupe to one copy; differing
    text -> keep both. Nothing is ever dropped.
    """
    merged = {}
    for source in (preserved, current):
        for conv_id, notes in source.items():
            bucket = merged.setdefault(conv_id, [])
            for note in notes:
                duplicate = any(
                    existing.get('note_id') == note.get('note_id')
                    and existing.get('text') == note.get('text')
                    for existing in bucket)
                if not duplicate:
                    bucket.append(note)
    return merged


def backup_notes(data_dir, retain=10):
    """Copy notes.json to <data_dir>/../backups/notes.json.bak.<ISO-stamp>.

    Called at the start of every pipeline run. Keeps the newest `retain`
    backups, pruning the oldest. Returns the backup path, or None if there
    is no notes.json to back up.
    """
    path = _notes_path(data_dir)
    if not os.path.exists(path):
        return None
    backups_dir = os.path.join(
        os.path.dirname(os.path.abspath(data_dir)), 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    dest = os.path.join(backups_dir, f'notes.json.bak.{_utc_stamp()}')
    shutil.copy2(path, dest)
    baks = sorted(glob.glob(os.path.join(backups_dir, 'notes.json.bak.*')))
    for old in baks[:-retain]:
        os.remove(old)
    return dest


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
