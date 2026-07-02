"""Regression tests: get_conversation must surface per-message timestamps
(Phase 0, Group 5 — resolves the `timestamp: ""` bug re-confirmed 2026-07-01).

Root cause was NOT the parsers (fixed in v4.4, commit 4fb8c26 — the on-disk
data carries a real 'timestamp' on every message): server/api.py's
get_conversation read m['created_at'] where messages store m['timestamp'].

Fixture-level tests cover the read-path mapping for both key shapes; the
E2E class spot-checks real conversations when data/ is present.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from server.api import SearchEngine  # noqa: E402


def make_fixture_data_dir(td, messages):
    """Minimal data/ directory: one conversation with `messages`."""
    data_dir = os.path.join(td, 'data')
    os.makedirs(data_dir)
    conv = {'id': 'fixture-conv-1', 'name': 'Fixture', 'provider': 'claude',
            'created_at': '2026-01-01T00:00:00Z', 'messages': messages}
    with open(os.path.join(data_dir, 'conversations.json'), 'w') as f:
        json.dump([conv], f)
    np.save(os.path.join(data_dir, 'embeddings.npy'),
            np.zeros((1, 384), dtype=np.float32))
    return data_dir


class TestGetConversationTimestamps(unittest.TestCase):
    def get_messages(self, stored_messages):
        with tempfile.TemporaryDirectory() as td:
            engine = SearchEngine(data_dir=make_fixture_data_dir(td, stored_messages))
            engine.load()
            result = engine.get_conversation('fixture-conv-1')
            self.assertNotIn('error', result)
            return result['messages']

    def test_timestamp_key_is_surfaced(self):
        """The post-v4.4 on-disk shape: {'role','text','timestamp'}."""
        msgs = self.get_messages([
            {'role': 'user', 'text': 'hi',
             'timestamp': '2026-03-01T10:00:00.123456+00:00'},
            {'role': 'assistant', 'text': 'hello',
             'timestamp': '2026-03-01T10:00:05.654321+00:00'},
        ])
        self.assertEqual(msgs[0]['timestamp'], '2026-03-01T10:00:00.123456+00:00')
        self.assertEqual(msgs[1]['timestamp'], '2026-03-01T10:00:05.654321+00:00')

    def test_legacy_created_at_fallback(self):
        """Pre-v4.4 data stored message times under 'created_at'."""
        msgs = self.get_messages([
            {'role': 'user', 'text': 'hi', 'created_at': '2025-01-01T00:00:00Z'},
        ])
        self.assertEqual(msgs[0]['timestamp'], '2025-01-01T00:00:00Z')

    def test_missing_timestamp_yields_empty_string(self):
        msgs = self.get_messages([{'role': 'user', 'text': 'hi'}])
        self.assertEqual(msgs[0]['timestamp'], '')


class TestRealDataSpotCheck(unittest.TestCase):
    """Spot-check three real conversations end-to-end (skips without data/)."""

    N_SPOT = 3

    @classmethod
    def setUpClass(cls):
        if not (REPO / 'data' / 'conversations.json').exists():
            raise unittest.SkipTest('No pre-computed data available')

    def test_three_known_conversations_have_iso_timestamps(self):
        engine = SearchEngine()
        engine.load()
        with open(REPO / 'data' / 'conversations.json') as f:
            convs = json.load(f)
        checked = 0
        for conv in convs:
            if checked >= self.N_SPOT:
                break
            if not conv.get('messages'):
                continue
            result = engine.get_conversation(conv['id'])
            self.assertNotIn('error', result)
            stamps = [m['timestamp'] for m in result['messages'] if m['timestamp']]
            self.assertTrue(stamps, f"conversation {conv['id']} has no timestamps at all")
            for ts in stamps:
                # ISO 8601: parseable by fromisoformat (Z-suffix normalized)
                from datetime import datetime
                datetime.fromisoformat(ts.replace('Z', '+00:00'))
            checked += 1
        self.assertEqual(checked, self.N_SPOT)


if __name__ == '__main__':
    unittest.main()
