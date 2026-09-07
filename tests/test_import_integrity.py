"""Import integrity tests: parser duplicate-ID contract + report consistency.

Contract under test (docs/superpowers/plans/2026-09-07-index-preservation.md,
conflicting-ID import contract). Upstream ``seen_ids`` in
``parse_chatgpt_export`` currently discards conflicting duplicate records
before shared reconciliation can see them — the parser tests here use the
REAL parser on synthetic raw ChatGPT mapping files and are red until the
minimal parser fix lands. The report tests are red until
scripts/ingest_openai_export measures corpus_before from the actual ingest
and survives a record-write failure after a successful commit.

All fixtures are synthetic temp directories; no sockets, no real corpus.
"""

import builtins
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('CONSTELLATION_DATA_DIR',
                      tempfile.mkdtemp(prefix='constellation-test-env-'))

from core.chatgpt_parser import parse_chatgpt_export  # noqa: E402
from core.ingest import ingest_conversations  # noqa: E402
from core.storage import load_snapshot  # noqa: E402
from tests.test_chatgpt_parser import (  # noqa: E402
    make_conversation, make_node, make_root_node)
from tests.test_ingest_preservation import (  # noqa: E402
    NOTES_BYTES, FakeEmbedder, make_conv, patched_graph)

import scripts.ingest_openai_export as openai_mod  # noqa: E402


def raw_conv(conv_id, texts, node_prefix='n', t0=1682369000.0):
    """Synthetic raw ChatGPT conversation: linear thread of `texts`
    (alternating user/assistant). Node ids and create_times derive from
    `node_prefix`/`t0`, so equivalent histories can carry unstable ids."""
    nodes = [make_root_node('root', children=[f'{node_prefix}0'])]
    for i, text in enumerate(texts):
        nodes.append(make_node(
            f'{node_prefix}{i}',
            parent='root' if i == 0 else f'{node_prefix}{i - 1}',
            children=[f'{node_prefix}{i + 1}'] if i < len(texts) - 1 else [],
            role='user' if i % 2 == 0 else 'assistant',
            text=text, create_time=t0 + i))
    return make_conversation(nodes, current_node=f'{node_prefix}{len(texts) - 1}',
                             conv_id=conv_id, title=f'Chat {conv_id}')


class ParserDuplicateContractTest(unittest.TestCase):
    """Real parser on raw mapping files: conflicting duplicate ids must
    raise, equivalent duplicates must coalesce exactly once."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='constellation-integrity-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write_shard(self, name, convs):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            json.dump(convs, f)
        return path

    def parse(self, path):
        with contextlib.redirect_stderr(io.StringIO()):
            return parse_chatgpt_export(path)

    def test_conflicting_duplicate_ids_same_file_raise(self):
        path = self.write_shard('conversations-000.json', [
            raw_conv('dup', ['one way', 're one'], node_prefix='a'),
            raw_conv('dup', ['ANOTHER way', 're another'], node_prefix='b'),
        ])
        with self.assertRaises(ValueError):
            self.parse(path)

    def test_conflicting_duplicate_ids_across_shards_raise(self):
        self.write_shard('conversations-000.json',
                         [raw_conv('dup', ['one way', 're one'],
                                   node_prefix='a')])
        self.write_shard('conversations-001.json',
                         [raw_conv('dup', ['ANOTHER way', 're another'],
                                   node_prefix='b')])
        with self.assertRaises(ValueError):
            self.parse(self.dir)

    def test_equivalent_duplicates_across_shards_coalesce_once(self):
        # Same role/text history; message node ids and create_times differ
        # (unstable per-message identity must not count as a conflict).
        self.write_shard('conversations-000.json',
                         [raw_conv('same', ['stable q', 'stable a'],
                                   node_prefix='a', t0=1682369000.0)])
        self.write_shard('conversations-001.json',
                         [raw_conv('same', ['stable q', 'stable a'],
                                   node_prefix='b', t0=1690000000.0)])
        result = self.parse(self.dir)
        self.assertEqual(len(result), 1)
        self.assertEqual([(m['role'], m['text'])
                          for m in result[0]['messages']],
                         [('user', 'stable q'), ('assistant', 'stable a')])

    def test_max_conversations_unchanged(self):
        self.write_shard('conversations-000.json',
                         [raw_conv('c1', ['first'], node_prefix='a')])
        self.write_shard('conversations-001.json',
                         [raw_conv('c2', ['second'], node_prefix='b')])
        result = parse_chatgpt_export(self.dir, max_conversations=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'c1')


class OpenAIReportConsistencyTest(unittest.TestCase):
    """The import report must state committed facts: corpus_before from the
    actual ingest attempt, absent_from_export from the committed snapshot,
    and a record_error (rc 0) when only the record write fails."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='constellation-report-')
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        with open(os.path.join(self.data_dir, 'notes.json'), 'wb') as f:
            f.write(NOTES_BYTES)
        with patched_graph():
            ingest_conversations(self.data_dir, [
                make_conv('a', ['alpha one'], provider='chatgpt'),
                make_conv('b', ['beta one'], provider='chatgpt'),
            ], FakeEmbedder())

    def run_main(self, export, extra_patches=()):
        argv = ['ingest_openai_export.py', '/synthetic/export',
                '--record-dir', self.data_dir]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', argv))
            stack.enter_context(mock.patch.object(
                openai_mod, 'DATA_DIR', self.data_dir))
            stack.enter_context(mock.patch.object(
                openai_mod.ni, 'DATA_DIR', self.data_dir))
            stack.enter_context(mock.patch.object(
                openai_mod, 'parse_export',
                lambda export_dir: (['shard-000'], dict(export))))
            stack.enter_context(mock.patch(
                'core.embedder.Embedder', FakeEmbedder))
            stack.enter_context(patched_graph())
            for patch in extra_patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            rc = openai_mod.main()
        return rc, json.loads(out.getvalue())

    def export(self):
        return {'a': make_conv('a', ['alpha one', 'alpha two'],
                               provider='chatgpt')}

    def concurrent_writer_patch(self, results):
        """Patch core.ingest.ingest_conversations so a separate synthetic
        import lands between the script's preview and its actual ingest."""
        def wrapper(data_dir, candidates, embedder, **kwargs):
            with patched_graph():
                ingest_conversations(
                    data_dir, [make_conv('w', ['written concurrently'],
                                         provider='chatgpt')], FakeEmbedder())
            result = ingest_conversations(data_dir, candidates, embedder,
                                          **kwargs)
            results.append(result)
            return result
        return mock.patch('core.ingest.ingest_conversations', wrapper)

    def test_corpus_before_reflects_actual_ingest_after_concurrent_write(self):
        results = []
        rc, report = self.run_main(
            self.export(), [self.concurrent_writer_patch(results)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(report['corpus_before'], results[0].before,
                         'corpus_before must equal the actual IngestResult'
                         '.before, not a pre-preview count')

    def test_absent_from_export_matches_committed_record(self):
        results = []
        rc, report = self.run_main(
            self.export(), [self.concurrent_writer_patch(results)])
        self.assertEqual(rc, 0)
        with open(report['absent_from_export_record']) as f:
            record = json.load(f)
        # Committed snapshot: chatgpt ids {a, b, w} minus export {a}.
        snap = load_snapshot(self.data_dir)
        committed_absent = sorted(
            c['id'] for c in snap.conversations
            if c.get('provider') == 'chatgpt' and c['id'] != 'a')
        self.assertEqual(record['conversation_ids'], committed_absent)
        self.assertEqual(record['count'], len(committed_absent))
        self.assertEqual(report['absent_from_export'], record['count'],
                         'report and record must describe the same '
                         'committed state')

    def test_record_write_failure_after_commit_reports_and_returns_zero(self):
        real_open = builtins.open

        def failing_open(path, *args, **kwargs):
            if os.path.basename(str(path)) == 'absent_from_export.json':
                raise OSError('simulated record write failure')
            return real_open(path, *args, **kwargs)

        rc, report = self.run_main(
            self.export(),
            [mock.patch('builtins.open', side_effect=failing_open)])
        self.assertEqual(rc, 0, 'record failure after a successful commit '
                                'must not fail the import')
        snap = load_snapshot(self.data_dir)
        self.assertEqual(report['generation'], snap.token)
        self.assertEqual(report['corpus_after'], len(snap.conversations))
        self.assertEqual([m['text'] for m in snap.conversations[0]['messages']
                          if m['role'] == 'user'],
                         ['alpha one', 'alpha two'])
        self.assertIn('record_error', report)


if __name__ == '__main__':
    unittest.main()
