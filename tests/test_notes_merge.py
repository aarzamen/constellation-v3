"""Regression suite for the notes-merge-on-reembed fix (Phase 0, Group 4).

Root cause (Mar 29 audit): a rebuild could leave notes.json freshly written
instead of merged, destroying every note written Mar 7-15. These tests lock
the fix:

  * merge_notes union semantics (incl. the mid-rebuild-note case)
  * backup_notes creates .bak files and retains exactly 10
  * corrupted notes.json is quarantined, never silently overwritten
  * E2E: a note written via SearchEngine.add_note() survives a real
    --reembed on the fixture corpus with identical note_id and text

`make merge-safe` runs THIS file and touches .claude/flags/MERGE_SAFE only
when it is green — that flag is what unlocks guard_bash rule 5.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.notes import (  # noqa: E402
    append_note, backup_notes, load_notes, merge_notes, save_notes,
)

SAMPLE = REPO / 'sample_data' / 'dummy_conversations.json'


def note(nid, text):
    return {'text': text, 'created_at': '2026-07-01T00:00:00+00:00', 'note_id': nid}


class TestMergeNotes(unittest.TestCase):
    def test_disjoint_conversations_union(self):
        merged = merge_notes({'a': [note('n1', 'x')]}, {'b': [note('n2', 'y')]})
        self.assertEqual(set(merged), {'a', 'b'})

    def test_preserved_only_note_survives(self):
        """The core Mar-29 case: rebuild wrote a fresh file, snapshot must win."""
        merged = merge_notes({'a': [note('n1', 'precious')]}, {})
        self.assertEqual(merged['a'][0]['text'], 'precious')

    def test_mid_rebuild_note_not_lost(self):
        """A note added between snapshot and merge (current-only) survives."""
        merged = merge_notes({'a': [note('n1', 'old')]},
                             {'a': [note('n1', 'old'), note('n2', 'mid-rebuild')]})
        self.assertEqual({n['note_id'] for n in merged['a']}, {'n1', 'n2'})

    def test_identical_collision_dedupes(self):
        merged = merge_notes({'a': [note('n1', 'same')]}, {'a': [note('n1', 'same')]})
        self.assertEqual(len(merged['a']), 1)

    def test_differing_collision_keeps_both(self):
        merged = merge_notes({'a': [note('n1', 'v1')]}, {'a': [note('n1', 'v2')]})
        self.assertEqual(len(merged['a']), 2)
        self.assertEqual({n['text'] for n in merged['a']}, {'v1', 'v2'})

    def test_empty_inputs(self):
        self.assertEqual(merge_notes({}, {}), {})


class TestBackupNotes(unittest.TestCase):
    def test_backup_created_and_pruned_to_ten(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = os.path.join(td, 'data')
            os.makedirs(data_dir)
            save_notes(data_dir, {'a': [note('n1', 'x')]})
            backups_dir = os.path.join(td, 'backups')
            # Simulate 12 prior runs (distinct stamped names)
            os.makedirs(backups_dir)
            for i in range(12):
                Path(backups_dir, f'notes.json.bak.2026010{i:02d}T000000Z').write_text('{}')
            dest = backup_notes(data_dir, retain=10)
            self.assertTrue(os.path.exists(dest))
            remaining = sorted(os.listdir(backups_dir))
            self.assertEqual(len(remaining), 10)
            self.assertIn(os.path.basename(dest), remaining)  # newest kept

    def test_no_notes_file_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = os.path.join(td, 'data')
            os.makedirs(data_dir)
            self.assertIsNone(backup_notes(data_dir))


class TestCorruptQuarantine(unittest.TestCase):
    def test_corrupt_file_is_quarantined_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = os.path.join(td, 'data')
            os.makedirs(data_dir)
            corrupt_path = os.path.join(data_dir, 'notes.json')
            Path(corrupt_path).write_text('{"a": [TRUNCATED')
            loaded = load_notes(data_dir)
            self.assertEqual(loaded, {})
            quarantined = [f for f in os.listdir(data_dir)
                           if f.startswith('notes.json.corrupt.')]
            self.assertEqual(len(quarantined), 1)
            content = Path(data_dir, quarantined[0]).read_text()
            self.assertIn('TRUNCATED', content)  # original bytes preserved


class TestReembedRegression(unittest.TestCase):
    """E2E: run the real pipeline twice on the fixture corpus with a note
    written in between; the note must survive the forced re-embed."""

    @classmethod
    def setUpClass(cls):
        if not SAMPLE.exists():
            raise unittest.SkipTest('Sample data not found')

    def _run_pipeline(self, data_dir, force_reembed):
        import launch
        from core.parser import parse_claude_export
        conversations = parse_claude_export(str(SAMPLE))
        config = {'source': {}}
        with mock.patch.object(launch, 'DATA_DIR', data_dir), \
             mock.patch.object(launch, 'ensure_data_dir', lambda: os.makedirs(data_dir, exist_ok=True)), \
             mock.patch.object(launch, 'save_config', lambda cfg: None):
            launch.run_pipeline_embed(conversations, str(SAMPLE), config,
                                      force_reembed=force_reembed)
        return conversations

    def test_note_survives_reembed_with_bak(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = os.path.join(td, 'data')

            # Initial pipeline run
            conversations = self._run_pipeline(data_dir, force_reembed=False)

            # Write a note through the real MCP-layer path
            from server.api import SearchEngine
            engine = SearchEngine(data_dir=data_dir)
            engine.load()
            target = conversations[0]['id']
            added = engine.add_note(target, 'REGRESSION-CANARY: must survive re-embed')
            self.assertNotIn('error', added)
            note_id = added['note']['note_id']

            # Forced re-embed — the Mar 29 destruction scenario
            self._run_pipeline(data_dir, force_reembed=True)

            after = load_notes(data_dir)
            surviving = {n['note_id']: n['text'] for n in after.get(target, [])}
            self.assertIn(note_id, surviving, 'note LOST on re-embed')
            self.assertEqual(surviving[note_id],
                             'REGRESSION-CANARY: must survive re-embed')

            # The pre-rebuild backup must exist as a sibling backups/ dir
            backups_dir = os.path.join(td, 'backups')
            baks = [f for f in os.listdir(backups_dir)
                    if f.startswith('notes.json.bak.')]
            self.assertGreaterEqual(len(baks), 1, 'no .bak created by pipeline run')


if __name__ == '__main__':
    unittest.main()
