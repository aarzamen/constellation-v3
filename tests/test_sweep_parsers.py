"""Tests for SWEEP v2 parsers: Abacus export + Gemini MyActivity.json extractor."""

import json
import os
import tempfile
import unittest

from core.abacus_parser import parse_abacus_export
from core.gemini_activity_parser import extract_entries_json, sessionize


class TestAbacus(unittest.TestCase):
    def _export(self):
        return {
            'export_metadata': {'source': 'Abacus.AI ChatLLM Teams'},
            'conversations': [
                {'uuid': 'u1', 'conversation_id': 'c1', 'name': 'Jetson hunt',
                 'created_at': '2025-01-08T23:48:17+00:00',
                 'chat_messages': [
                     {'sender': 'human', 'role': 'human', 'text': 'find cheap jetsons',
                      'created_at': '2025-01-08T23:48:17+00:00', 'segments': []},
                     {'sender': 'assistant', 'role': 'assistant', 'text': 'here you go',
                      'created_at': '2025-01-08T23:49:00+00:00', 'segments': []},
                 ]},
                {'uuid': 'u2', 'conversation_id': 'c2', 'name': 'Empty',
                 'created_at': '', 'chat_messages': []},
            ],
        }

    def test_parse_maps_roles_and_skips_empty(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'abacus.json')
            json.dump(self._export(), open(p, 'w'))
            convs = parse_abacus_export(p)
            self.assertEqual(len(convs), 1)                 # empty conv skipped
            c = convs[0]
            self.assertEqual(c['provider'], 'abacus')
            self.assertEqual(c['id'], 'abacus_c1')          # conversation_id preferred
            self.assertEqual([m['role'] for m in c['messages']], ['user', 'assistant'])
            self.assertEqual(c['user_messages'], ['find cheap jetsons'])


class TestGeminiJSON(unittest.TestCase):
    def _records(self):
        return [
            {'header': 'Gemini Apps', 'title': 'Prompted first question',
             'time': '2026-07-04T05:00:00.000Z',
             'safeHtmlItem': [{'html': '<p>answer one</p>'}]},
            {'header': 'Gemini Apps', 'title': 'Said second question',
             'time': '2026-07-04T05:20:00.000Z',
             'safeHtmlItem': [{'html': '<p>answer two</p>'}]},
            {'header': 'Gemini Apps', 'title': 'Prompted later question',
             'time': '2026-07-04T09:00:00.000Z', 'safeHtmlItem': []},
        ]

    def test_extract_and_sessionize(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'MyActivity.json')
            json.dump(self._records(), open(p, 'w'))
            entries = extract_entries_json(p)
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0]['prompt'], 'first question')   # 'Prompted ' stripped
            self.assertEqual(entries[1]['prompt'], 'second question')  # 'Said ' stripped
            self.assertEqual(entries[0]['response'], 'answer one')     # html cleaned
            self.assertEqual(entries[0]['tz'], 'UTC')
            convs = sessionize(entries, gap_minutes=30)
            self.assertEqual(len(convs), 2)                            # 30-min gap split
            self.assertTrue(all(c['provider'] == 'gemini' for c in convs))

    def test_cutoff_filtering(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'MyActivity.json')
            json.dump(self._records(), open(p, 'w'))
            cutoff = '2026-07-04T05:20:00.000000'   # exclude boundary, keep only 09:00
            entries = [e for e in extract_entries_json(p) if e['iso'] > cutoff]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['prompt'], 'later question')


if __name__ == '__main__':
    unittest.main()
