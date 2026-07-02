"""Tests for the nightly Claude Code ingest (Phase 2).

Covers the three invariants the pipeline must hold:
  * manifest high-water classification (new / changed / unchanged),
  * claude-code parser idempotency + extraction rules,
  * notes.json untouched across an ingest cycle.

The parser + classify tests are model-free. The notes-untouched test drives the
real run() orchestration with a stubbed embed step (no model), asserting the
snapshot/assert logic and that the orchestration never writes notes.json. The
real embed path was additionally confirmed to preserve notes.json byte-for-byte
in a live 28-conversation ingest (see BASELINE-AIR Phase 2).
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'scripts'))
sys.path.insert(0, str(REPO))

import nightly_ingest as ni  # noqa: E402
from core.claude_code_parser import parse_session_file  # noqa: E402


def _write_session(path, records):
    with open(path, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')


USER = lambda t, ts='2026-01-01T00:00:00Z': {
    'type': 'user', 'timestamp': ts,
    'message': {'role': 'user', 'content': t}}
ASST = lambda blocks, ts='2026-01-01T00:00:01Z': {
    'type': 'assistant', 'timestamp': ts,
    'message': {'role': 'assistant', 'content': blocks}}


class TestClassify(unittest.TestCase):
    def test_new_changed_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            a = os.path.join(td, 'aaa.jsonl')  # unchanged
            b = os.path.join(td, 'bbb.jsonl')  # changed
            c = os.path.join(td, 'ccc.jsonl')  # new
            for p in (a, b, c):
                _write_session(p, [USER('hello there')])
            by_id = {
                'local_aaa': {'path': a, 'mtime': 1.0, 'conv_id': 'local_aaa'},
                'local_bbb': {'path': b, 'mtime': 1.0, 'conv_id': 'local_bbb'},
                'local_ccc': {'path': c, 'mtime': 1.0, 'conv_id': 'local_ccc'},
            }
            manifest = {
                'local_aaa': {'sha256': ni._sha256(a)},        # matches -> unchanged
                'local_bbb': {'sha256': 'deadbeef' * 8},       # differs -> changed
                # local_ccc absent -> new
            }
            new, changed, unchanged = ni.classify(by_id, manifest)
            self.assertEqual({e['conv_id'] for e in new}, {'local_ccc'})
            self.assertEqual({e['conv_id'] for e in changed}, {'local_bbb'})
            self.assertEqual({e['conv_id'] for e in unchanged}, {'local_aaa'})
            # sha256 populated on every entry
            self.assertTrue(all('sha256' in e for e in by_id.values()))

    def test_idempotent_after_manifest_write(self):
        with tempfile.TemporaryDirectory() as td:
            a = os.path.join(td, 'aaa.jsonl')
            _write_session(a, [USER('hello')])
            by_id = {'local_aaa': {'path': a, 'mtime': 1.0, 'conv_id': 'local_aaa'}}
            # first pass: new; then record sha and re-classify -> unchanged
            new, _, _ = ni.classify(by_id, {})
            self.assertEqual(len(new), 1)
            manifest = {'local_aaa': {'sha256': by_id['local_aaa']['sha256']}}
            new2, changed2, unchanged2 = ni.classify(by_id, manifest)
            self.assertEqual((len(new2), len(changed2), len(unchanged2)), (0, 0, 1))


class TestParserIdempotencyAndExtraction(unittest.TestCase):
    def _sample(self, path):
        _write_session(path, [
            {'type': 'ai-title', 'aiTitle': 'My Coding Session'},
            USER('first user prompt'),
            {'type': 'user', 'timestamp': 't', 'isMeta': True,
             'message': {'role': 'user', 'content': 'META NOISE'}},
            ASST([{'type': 'thinking', 'thinking': 'secret reasoning'},
                  {'type': 'text', 'text': 'assistant reply'},
                  {'type': 'tool_use', 'name': 'Bash', 'input': {}}]),
            {'type': 'user', 'timestamp': 't2',
             'message': {'role': 'user', 'content': [
                 {'type': 'tool_result', 'content': 'TOOL OUTPUT'}]}},
            {'type': 'file-history-snapshot', 'foo': 'bar'},
        ])

    def test_extraction_rules(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'sess-uuid.jsonl')
            self._sample(p)
            conv = parse_session_file(p)
            self.assertEqual(conv['id'], 'local_sess-uuid')
            self.assertEqual(conv['provider'], 'claude-code')
            self.assertEqual(conv['name'], 'My Coding Session')
            texts = [m['text'] for m in conv['messages']]
            self.assertIn('first user prompt', texts)
            self.assertIn('assistant reply', texts)
            # meta, thinking, tool_use, tool_result all dropped
            joined = ' '.join(texts)
            for noise in ('META NOISE', 'secret reasoning', 'TOOL OUTPUT'):
                self.assertNotIn(noise, joined)
            self.assertEqual(conv['user_messages'], ['first user prompt'])

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'sess-uuid.jsonl')
            self._sample(p)
            a = parse_session_file(p)
            b = parse_session_file(p)
            self.assertEqual(json.dumps(a, sort_keys=True),
                             json.dumps(b, sort_keys=True))

    def test_empty_session_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'empty.jsonl')
            _write_session(p, [{'type': 'file-history-snapshot'},
                               {'type': 'system', 'x': 1}])
            self.assertIsNone(parse_session_file(p))


class TestNotesUntouchedInvariant(unittest.TestCase):
    """Drive run() with the embed step stubbed (no model). notes.json must be
    byte-identical before/after and the report must flag notes_untouched."""

    def setUp(self):
        self._saved = {k: getattr(ni, k) for k in
                       ('DATA_DIR', 'LOCAL_PROJECTS', 'STAGING', 'MANIFEST_PATH',
                        'LOCK_PATH', 'REPORT_DIR', 'pull_mbp', 'incremental_embed')}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(ni, k, v)

    def test_notes_untouched_across_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / 'data'
            projects = Path(td) / 'projects' / 'proj'
            data.mkdir(parents=True)
            projects.mkdir(parents=True)
            # seed a notes.json we will assert is untouched
            notes = {'someconv': [{'text': 'keep me', 'created_at': 'x',
                                   'note_id': 'abc12345'}]}
            (data / 'notes.json').write_text(json.dumps(notes, indent=2))
            notes_sha = hashlib.sha256((data / 'notes.json').read_bytes()).hexdigest()
            # a minimal existing corpus (only needed if embed runs)
            (data / 'conversations.json').write_text('[]')
            # one local session to ingest
            _write_session(projects / 'sess-1.jsonl',
                           [USER('a real coding prompt about pipelines')])

            ni.DATA_DIR = str(data)
            ni.LOCAL_PROJECTS = str(Path(td) / 'projects')
            ni.STAGING = str(Path(td) / 'staging')
            ni.MANIFEST_PATH = str(data / 'nightly_manifest.json')
            ni.LOCK_PATH = str(data / 'nightly.lock')
            ni.REPORT_DIR = str(data / 'logs' / 'nightly')
            ni.pull_mbp = lambda report: False  # no network
            # stub the embed: simulate a corpus change WITHOUT the model and
            # WITHOUT touching notes.json.
            def fake_embed(fresh_convs, embedder):
                (data / 'conversations.json').write_text(
                    json.dumps([{'id': c['id']} for c in fresh_convs]))
                return 0, len(fresh_convs)
            ni.incremental_embed = fake_embed

            report = ni.run(dry_run=False)

            self.assertEqual(report['new'], 1)
            self.assertGreaterEqual(report['embedded'], 1)
            self.assertTrue(report['notes_untouched'])
            after_sha = hashlib.sha256((data / 'notes.json').read_bytes()).hexdigest()
            self.assertEqual(notes_sha, after_sha)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / 'data'
            projects = Path(td) / 'projects' / 'proj'
            data.mkdir(parents=True)
            projects.mkdir(parents=True)
            (data / 'notes.json').write_text('{}')
            _write_session(projects / 'sess-1.jsonl', [USER('hello world')])
            ni.DATA_DIR = str(data)
            ni.LOCAL_PROJECTS = str(Path(td) / 'projects')
            ni.STAGING = str(Path(td) / 'staging')
            ni.MANIFEST_PATH = str(data / 'nightly_manifest.json')
            ni.LOCK_PATH = str(data / 'nightly.lock')
            ni.REPORT_DIR = str(data / 'logs' / 'nightly')
            ni.pull_mbp = lambda report: False

            report = ni.run(dry_run=True)
            self.assertEqual(report['new'], 1)
            self.assertIn('dry-run', report['action'])
            # manifest not created on a dry run
            self.assertFalse(os.path.exists(ni.MANIFEST_PATH))


class TestHotReload(unittest.TestCase):
    """SearchEngine hot-reloads when conversations.json mtime advances (P2-c)."""

    def _seed(self, data, convs):
        import numpy as np
        (data / 'conversations.json').write_text(json.dumps(convs))
        np.save(str(data / 'embeddings.npy'),
                np.random.RandomState(0).rand(len(convs), 384).astype('float32'))

    def test_reload_on_mtime_change(self):
        import time
        from server.api import SearchEngine
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            c1 = [{'id': 'a', 'name': 'one', 'provider': 'claude',
                   'messages': [{'role': 'user', 'text': 'hello', 'timestamp': ''}]},
                  {'id': 'b', 'name': 'two', 'provider': 'claude',
                   'messages': [{'role': 'user', 'text': 'world', 'timestamp': ''}]}]
            self._seed(data, c1)
            eng = SearchEngine(data_dir=str(data))
            eng.load()
            self.assertEqual(len(eng.conversations), 2)
            first_mtime = eng._loaded_mtime

            # grow the corpus; bump mtime the way the ingest does
            time.sleep(0.01)
            c2 = c1 + [{'id': 'c', 'name': 'three', 'provider': 'claude-code',
                        'messages': [{'role': 'user', 'text': 'new', 'timestamp': ''}]}]
            import numpy as np
            (data / 'conversations.json').write_text(json.dumps(c2))
            np.save(str(data / 'embeddings.npy'),
                    np.random.RandomState(1).rand(3, 384).astype('float32'))
            os.utime(str(data / 'conversations.json'), None)

            eng.load()  # next request -> hot reload
            self.assertEqual(len(eng.conversations), 3)
            self.assertNotEqual(eng._loaded_mtime, first_mtime)
            self.assertIn('c', eng.conversation_index)

    def test_no_reload_when_unchanged(self):
        from server.api import SearchEngine
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            self._seed(data, [{'id': 'a', 'name': 'one', 'provider': 'claude',
                               'messages': []}])
            eng = SearchEngine(data_dir=str(data))
            eng.load()
            marker = eng._loaded_mtime
            eng.load()  # no mtime change -> no reload, same marker
            self.assertEqual(eng._loaded_mtime, marker)


if __name__ == '__main__':
    unittest.main()
