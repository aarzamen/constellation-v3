"""Sidecar note persistence for Constellation.

Notes are stored in data/notes.json, separate from conversations.json,
so they survive pipeline rebuilds (--reembed).

Every mutation (append/delete/save, plus the read-triggered legacy
migration) holds a cross-process advisory lock (fcntl.flock on
data/notes.lock) across its whole read-modify-replace, so concurrent
writers serialize instead of overwriting each other's saves. POSIX only
(macOS/Linux); the lock file is stable and never removed.
"""

import contextlib
import datetime
import fcntl
import glob
import json
import os
import shutil
import sys
import tempfile
import uuid


class NotesCorruptError(ValueError):
    """notes.json exists but is not a parseable top-level JSON object."""


def _notes_path(data_dir):
    return os.path.join(data_dir, 'notes.json')


def _lock_path(data_dir):
    return os.path.join(data_dir, 'notes.lock')


def _utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


@contextlib.contextmanager
def _notes_lock(data_dir):
    """Exclusive cross-process advisory lock on the notes sidecar.

    Callers must never nest it — flock on a second descriptor blocks even
    within one process — which is why the _unlocked helpers exist.
    """
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(_lock_path(data_dir), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_notes_unlocked(path):
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        raise NotesCorruptError(str(e)) from e
    if not isinstance(data, dict):
        # A wrong top-level shape would otherwise surface later as an
        # AttributeError mid-mutation; treat it as corruption up front.
        raise NotesCorruptError(
            f'top-level JSON is {type(data).__name__}, expected object')
    return data


def _quarantine_unlocked(path, error):
    quarantine = f'{path}.corrupt.{_utc_stamp()}'
    try:
        shutil.copy2(path, quarantine)
        print(f"Warning: notes.json is corrupted ({error}); "
              f"quarantined a copy at {quarantine}", file=sys.stderr)
    except OSError:
        print(f"Warning: notes.json is corrupted and could not be "
              f"quarantined: {error}", file=sys.stderr)


def _load_notes_unlocked(data_dir, for_mutation):
    """Read the sidecar. Caller must hold the notes lock."""
    path = _notes_path(data_dir)
    if not os.path.exists(path):
        _migrate_legacy_notes_unlocked(data_dir)
        if not os.path.exists(path):
            return {}
    try:
        return _read_notes_unlocked(path)
    except NotesCorruptError as e:
        _quarantine_unlocked(path, e)
        if for_mutation:
            # Refuse the mutation: a failed (or even successful) quarantine
            # never authorizes replacing the only copy of the user's words.
            raise
        return {}


def _save_notes_unlocked(data_dir, notes_dict):
    """Durable atomic publish. Caller must hold the notes lock."""
    path = _notes_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=data_dir, prefix='.notes-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(notes_dict, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_notes(data_dir):
    """Load notes from sidecar file. Returns empty dict if missing.

    A corrupt or wrongly shaped file is quarantined (best effort) and read
    as {} for backward compatibility; mutations refuse instead of
    overwriting it.
    """
    with _notes_lock(data_dir):
        return _load_notes_unlocked(data_dir, for_mutation=False)


def save_notes(data_dir, notes_dict):
    """Atomically write notes dict to sidecar file.

    Serialized against all other note mutations. Raises NotesCorruptError
    rather than replacing an unparseable original, even if the quarantine
    copy fails.
    """
    with _notes_lock(data_dir):
        path = _notes_path(data_dir)
        if os.path.exists(path):
            try:
                _read_notes_unlocked(path)
            except NotesCorruptError as e:
                _quarantine_unlocked(path, e)
                raise
        _save_notes_unlocked(data_dir, notes_dict)


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
    with _notes_lock(data_dir):
        notes = _load_notes_unlocked(data_dir, for_mutation=True)
        bucket = notes.setdefault(conversation_id, [])
        note = {
            'text': text,
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'note_id': uuid.uuid4().hex[:8],
        }
        bucket.append(note)
        _save_notes_unlocked(data_dir, notes)
    return note


def delete_note(data_dir, conversation_id, note_id):
    """Delete a note by note_id. Returns True if found and deleted."""
    with _notes_lock(data_dir):
        notes = _load_notes_unlocked(data_dir, for_mutation=True)
        conv_notes = notes.get(conversation_id, [])
        for i, n in enumerate(conv_notes):
            if n.get('note_id') == note_id:
                conv_notes.pop(i)
                if not conv_notes:
                    del notes[conversation_id]
                _save_notes_unlocked(data_dir, notes)
                return True
    return False


def get_notes_for_conversation(data_dir, conversation_id):
    """Get all notes for a conversation."""
    return load_notes(data_dir).get(conversation_id, [])


def _migrate_legacy_notes_unlocked(data_dir):
    """One-time migration: pull notes from conversations.json into sidecar.

    Caller must hold the notes lock. Rechecks the sidecar first: if another
    writer created it while we waited for the lock, migrating now would
    overwrite their just-added notes.
    """
    if os.path.exists(_notes_path(data_dir)):
        return
    conv_path = os.path.join(data_dir, 'conversations.json')
    if not os.path.exists(conv_path):
        return

    try:
        with open(conv_path, 'r') as f:
            conversations = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(conversations, list):
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
        _save_notes_unlocked(data_dir, migrated)
        print(f"Migrated {sum(len(v) for v in migrated.values())} legacy notes to notes.json", file=sys.stderr)
