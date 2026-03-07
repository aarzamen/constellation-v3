"""Tests for sidecar note persistence and browsing tools."""

import json
import os
import sys
import tempfile
import shutil
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notes import load_notes, save_notes, append_note, delete_note, get_notes_for_conversation


class TestNotesPersistence(unittest.TestCase):
    """Test core/notes.py sidecar persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_load_notes_missing_file(self):
        """load_notes returns empty dict when notes.json doesn't exist."""
        result = load_notes(self.tmpdir)
        self.assertEqual(result, {})

    def test_append_note_creates_file(self):
        """append_note creates notes.json and adds a note with correct structure."""
        note = append_note(self.tmpdir, 'conv-1', 'Test note')
        self.assertIn('text', note)
        self.assertIn('created_at', note)
        self.assertIn('note_id', note)
        self.assertEqual(note['text'], 'Test note')
        self.assertEqual(len(note['note_id']), 8)

        # File should exist
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, 'notes.json')))

    def test_append_note_accumulates(self):
        """Multiple notes on same conversation accumulate."""
        append_note(self.tmpdir, 'conv-1', 'Note 1')
        append_note(self.tmpdir, 'conv-1', 'Note 2')
        append_note(self.tmpdir, 'conv-1', 'Note 3')

        notes = get_notes_for_conversation(self.tmpdir, 'conv-1')
        self.assertEqual(len(notes), 3)
        self.assertEqual(notes[0]['text'], 'Note 1')
        self.assertEqual(notes[2]['text'], 'Note 3')

    def test_delete_note_removes_correct_note(self):
        """delete_note removes the right note by note_id."""
        n1 = append_note(self.tmpdir, 'conv-1', 'Note 1')
        n2 = append_note(self.tmpdir, 'conv-1', 'Note 2')

        result = delete_note(self.tmpdir, 'conv-1', n1['note_id'])
        self.assertTrue(result)

        remaining = get_notes_for_conversation(self.tmpdir, 'conv-1')
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['note_id'], n2['note_id'])

    def test_delete_note_bad_id_returns_false(self):
        """delete_note with nonexistent note_id returns False."""
        append_note(self.tmpdir, 'conv-1', 'Note 1')
        result = delete_note(self.tmpdir, 'conv-1', 'badid123')
        self.assertFalse(result)

    def test_delete_note_bad_conversation_returns_false(self):
        """delete_note with nonexistent conversation returns False."""
        result = delete_note(self.tmpdir, 'nonexistent', 'badid123')
        self.assertFalse(result)

    def test_notes_survive_conversations_json_rebuild(self):
        """Simulate re-embed: delete and recreate conversations.json, verify notes intact."""
        # Create a conversations.json
        conv_path = os.path.join(self.tmpdir, 'conversations.json')
        with open(conv_path, 'w') as f:
            json.dump([{'id': 'conv-1', 'name': 'Test'}], f)

        # Add notes via sidecar
        append_note(self.tmpdir, 'conv-1', 'Important note')

        # Simulate re-embed: overwrite conversations.json (no notes field)
        with open(conv_path, 'w') as f:
            json.dump([{'id': 'conv-1', 'name': 'Test Rebuilt'}], f)

        # Notes should still be there
        notes = get_notes_for_conversation(self.tmpdir, 'conv-1')
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['text'], 'Important note')

    def test_atomic_write_no_partial(self):
        """save_notes uses atomic write — no .tmp file left behind."""
        notes = {'conv-1': [{'text': 'test', 'created_at': '', 'note_id': 'abc12345'}]}
        save_notes(self.tmpdir, notes)

        # .tmp file should not exist after successful write
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, 'notes.json.tmp')))
        # Main file should exist and be valid
        loaded = load_notes(self.tmpdir)
        self.assertEqual(loaded, notes)

    def test_load_notes_corrupted_json(self):
        """load_notes handles corrupted JSON gracefully."""
        path = os.path.join(self.tmpdir, 'notes.json')
        with open(path, 'w') as f:
            f.write('{bad json')

        result = load_notes(self.tmpdir)
        self.assertEqual(result, {})

    def test_migrate_legacy_notes(self):
        """Notes in conversations.json are migrated to sidecar on first load."""
        conv_path = os.path.join(self.tmpdir, 'conversations.json')
        with open(conv_path, 'w') as f:
            json.dump([{
                'id': 'conv-1',
                'name': 'Test',
                'notes': [
                    {'text': 'Legacy note', 'created_at': '2025-01-01T00:00:00Z'}
                ]
            }], f)

        # First load should trigger migration
        notes = load_notes(self.tmpdir)
        self.assertIn('conv-1', notes)
        self.assertEqual(len(notes['conv-1']), 1)
        self.assertEqual(notes['conv-1'][0]['text'], 'Legacy note')
        self.assertIn('note_id', notes['conv-1'][0])

    def test_get_notes_empty_conversation(self):
        """get_notes_for_conversation returns empty list for unknown conversation."""
        result = get_notes_for_conversation(self.tmpdir, 'nonexistent')
        self.assertEqual(result, [])

    def test_multiple_conversations_independent(self):
        """Notes for different conversations are independent."""
        append_note(self.tmpdir, 'conv-1', 'Note A')
        append_note(self.tmpdir, 'conv-2', 'Note B')

        self.assertEqual(len(get_notes_for_conversation(self.tmpdir, 'conv-1')), 1)
        self.assertEqual(len(get_notes_for_conversation(self.tmpdir, 'conv-2')), 1)

        delete_note(self.tmpdir, 'conv-1',
                    get_notes_for_conversation(self.tmpdir, 'conv-1')[0]['note_id'])
        self.assertEqual(len(get_notes_for_conversation(self.tmpdir, 'conv-1')), 0)
        self.assertEqual(len(get_notes_for_conversation(self.tmpdir, 'conv-2')), 1)


class TestBrowsingTools(unittest.TestCase):
    """Test list_conversations and enriched results."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create minimal data files for SearchEngine
        convs = []
        for i in range(10):
            convs.append({
                'id': f'conv-{i}',
                'name': f'Conversation {chr(65 + (9 - i))}',  # Z, Y, X, ... A ordering
                'created_at': f'2025-03-{10 + i:02d}T00:00:00Z',
                'provider': 'claude',
                'messages': [{'role': 'user', 'text': f'msg {j}'} for j in range(i + 1)],
            })
        with open(os.path.join(self.tmpdir, 'conversations.json'), 'w') as f:
            json.dump(convs, f)

        # Minimal embeddings
        np.save(os.path.join(self.tmpdir, 'embeddings.npy'), np.random.randn(10, 384))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _make_engine(self):
        from server.api import SearchEngine
        engine = SearchEngine(self.tmpdir)
        engine.load()
        return engine

    def test_list_conversations_structure(self):
        """list_conversations returns correct structure."""
        engine = self._make_engine()
        result = engine.list_conversations()
        self.assertIn('conversations', result)
        self.assertIn('total', result)
        self.assertIn('offset', result)
        self.assertIn('limit', result)
        self.assertEqual(result['total'], 10)
        # Each item has required fields
        item = result['conversations'][0]
        self.assertIn('id', item)
        self.assertIn('title', item)
        self.assertIn('date', item)
        self.assertIn('message_count', item)
        self.assertIn('has_notes', item)
        self.assertIn('cluster_id', item)

    def test_sort_by_date(self):
        """Default sort is by date, newest first."""
        engine = self._make_engine()
        result = engine.list_conversations(sort_by='date')
        dates = [c['date'] for c in result['conversations']]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_sort_by_title(self):
        """Sort by title is alphabetical."""
        engine = self._make_engine()
        result = engine.list_conversations(sort_by='title')
        titles = [c['title'] for c in result['conversations']]
        self.assertEqual(titles, sorted(titles, key=str.lower))

    def test_sort_by_message_count(self):
        """Sort by message_count is descending."""
        engine = self._make_engine()
        result = engine.list_conversations(sort_by='message_count')
        counts = [c['message_count'] for c in result['conversations']]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_pagination_offset_limit(self):
        """Offset and limit work correctly."""
        engine = self._make_engine()
        result = engine.list_conversations(offset=5, limit=3)
        self.assertEqual(len(result['conversations']), 3)
        self.assertEqual(result['offset'], 5)
        self.assertEqual(result['limit'], 3)
        self.assertEqual(result['total'], 10)

    def test_limit_cap_at_50(self):
        """Limit is capped at 50."""
        engine = self._make_engine()
        result = engine.list_conversations(limit=100)
        self.assertEqual(result['limit'], 50)

    def test_get_stats_keys(self):
        """get_stats returns expected keys."""
        engine = self._make_engine()
        stats = engine.get_stats()
        self.assertIn('totalConversations', stats)
        self.assertIn('totalMessages', stats)
        self.assertIn('dateRange', stats)
        self.assertIn('embeddingModel', stats)
        self.assertIn('embeddingDim', stats)

    def test_get_conversation_includes_notes_and_cluster(self):
        """get_conversation response includes notes and cluster_id."""
        engine = self._make_engine()
        # Add a note
        engine.add_note('conv-0', 'Test note')
        result = engine.get_conversation('conv-0')
        self.assertIn('notes', result)
        self.assertIn('cluster_id', result)
        self.assertEqual(len(result['notes']), 1)

    def test_delete_note_via_engine(self):
        """SearchEngine.delete_note works end-to-end."""
        engine = self._make_engine()
        add_result = engine.add_note('conv-0', 'To delete')
        note_id = add_result['note']['note_id']
        del_result = engine.delete_note('conv-0', note_id)
        self.assertEqual(del_result['status'], 'success')
        self.assertEqual(len(del_result['notes']), 0)

    def test_delete_note_not_found(self):
        """SearchEngine.delete_note returns error for bad note_id."""
        engine = self._make_engine()
        result = engine.delete_note('conv-0', 'badid123')
        self.assertIn('error', result)


if __name__ == '__main__':
    unittest.main()
