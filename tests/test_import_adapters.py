"""Adapter regression tests: launch, nightly, OpenAI and sweep imports must
route through the shared ingest pipeline (core.ingest.ingest_conversations)
and stop filtering out existing IDs.

Contract under test (docs/superpowers/plans/2026-09-07-index-preservation.md,
Task 2). Red until the adapters are rewired. All fixtures are synthetic temp
directories; graph/embed seams are faked; no real discovery, extraction,
rsync/ssh pulls or converters ever run. Assertions read the committed
snapshot through the storage API, never the incoming source dicts.
"""

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
# Keep any import-time data-dir resolution away from real user data.
os.environ.setdefault('CONSTELLATION_DATA_DIR',
                      tempfile.mkdtemp(prefix='constellation-test-env-'))

from core.ingest import ingest_conversations  # noqa: E402
from core.storage import load_snapshot  # noqa: E402
from tests.test_ingest_preservation import (  # noqa: E402
    NOTES_BYTES, FakeEmbedder, make_conv, patched_graph)

import launch  # noqa: E402
import scripts.nightly_ingest as ni  # noqa: E402
import scripts.sweep_ingest as sweep  # noqa: E402
import scripts.ingest_openai_export as openai_mod  # noqa: E402


def user_texts(conv):
    return [m['text'] for m in conv['messages'] if m.get('role') == 'user']


class AdapterTestCase(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='constellation-adapter-')
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        with open(os.path.join(self.data_dir, 'notes.json'), 'wb') as f:
            f.write(NOTES_BYTES)

    def seed(self, convs=None):
        """Seed a generation-based archive via the shared ingest (no flat
        files), so adapters that still read flat conversations.json go red."""
        convs = convs if convs is not None else [
            make_conv('a', ['alpha one', 'alpha two']),
            make_conv('b', ['beta one'], provider='chatgpt'),
        ]
        with patched_graph():
            result = ingest_conversations(self.data_dir, convs, FakeEmbedder())
        return result.snapshot

    def snapshot(self):
        snap = load_snapshot(self.data_dir)
        self.assertIsNotNone(snap, 'no committed snapshot found')
        return snap

    def conv_by_id(self, snap, cid):
        for c in snap.conversations:
            if c['id'] == cid:
                return c
        self.fail(f'conversation {cid} missing from committed snapshot')

    def quiet(self, stack):
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))


class TestLaunchAdapter(AdapterTestCase):
    """launch.run_pipeline_embed must use the shared ingest."""

    def run_pipeline(self, conversations, force=False):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                launch, 'DATA_DIR', self.data_dir))
            stack.enter_context(mock.patch.object(
                launch, 'ensure_data_dir',
                lambda: os.makedirs(self.data_dir, exist_ok=True)))
            stack.enter_context(mock.patch.object(
                launch, 'save_config', lambda config: None))
            stack.enter_context(mock.patch(
                'core.embedder.Embedder', FakeEmbedder))
            stack.enter_context(patched_graph())
            self.quiet(stack)
            return launch.run_pipeline_embed(
                conversations, '/synthetic/source.json',
                {'source': {}, 'embedding': {}}, force_reembed=force)

    def test_forced_rebuild_retains_source_absent_records(self):
        self.seed()
        self.run_pipeline([make_conv('a', ['alpha one', 'alpha two'])],
                          force=True)
        snap = self.snapshot()
        b = self.conv_by_id(snap, 'b')
        self.assertEqual(b['provider'], 'chatgpt')
        self.assertEqual(user_texts(b), ['beta one'])
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')),
                         ['alpha one', 'alpha two'])

    def test_extended_existing_id_processed_without_force(self):
        self.seed()
        texts = ['alpha one', 'alpha two', 'alpha three']
        self.run_pipeline([make_conv('a', texts)])
        snap = self.snapshot()
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')), texts)
        self.assertEqual(user_texts(self.conv_by_id(snap, 'b')), ['beta one'])


class TestNightlyAdapter(AdapterTestCase):
    """nightly.incremental_embed feeds fresh candidates to the shared
    ingest and works against a generation-based archive (no flat files)."""

    def test_incremental_embed_reconciles_and_reports_snapshot(self):
        self.seed()
        fresh = [make_conv('a', ['alpha one', 'alpha two', 'alpha three']),
                 make_conv('local_c', ['claude code session'],
                           provider='claude-code')]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                ni, 'DATA_DIR', self.data_dir))
            stack.enter_context(patched_graph())
            self.quiet(stack)
            result = ni.incremental_embed(fresh, FakeEmbedder())

        before, after = result  # legacy tuple unpack still supported
        self.assertEqual((before, after), (2, 3))
        snap = result.snapshot  # committed snapshot report state
        self.assertEqual(snap.token, self.snapshot().token)
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')),
                         ['alpha one', 'alpha two', 'alpha three'])
        self.assertEqual(user_texts(self.conv_by_id(snap, 'b')), ['beta one'])
        self.conv_by_id(snap, 'local_c')


class TestOpenAIAdapter(AdapterTestCase):
    """scripts.ingest_openai_export must pass the full export (including
    existing IDs) to the shared ingest and report committed counts."""

    def seed_openai(self):
        return self.seed([
            make_conv('a', ['alpha one', 'alpha two'], provider='chatgpt'),
            make_conv('b', ['beta one'], provider='chatgpt'),
            make_conv('k', ['kappa one'], provider='claude'),
        ])

    def run_main(self, export, dry_run=False):
        argv = ['ingest_openai_export.py', '/synthetic/export',
                '--record-dir', self.data_dir]
        if dry_run:
            argv.append('--dry-run')
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
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            rc = openai_mod.main()
        return rc, json.loads(out.getvalue())

    def export_extended_plus_new(self):
        return {
            'a': make_conv('a', ['alpha one', 'alpha two', 'alpha three'],
                           provider='chatgpt'),
            'n': make_conv('n', ['nu one'], provider='chatgpt'),
        }

    def test_extended_existing_id_committed_with_factual_counts(self):
        self.seed_openai()
        rc, report = self.run_main(self.export_extended_plus_new())
        self.assertEqual(rc, 0)
        snap = self.snapshot()
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')),
                         ['alpha one', 'alpha two', 'alpha three'])
        self.conv_by_id(snap, 'n')
        # absent-from-export chatgpt conv and other-provider conv retained
        self.assertEqual(user_texts(self.conv_by_id(snap, 'b')), ['beta one'])
        self.conv_by_id(snap, 'k')
        self.assertEqual(report['corpus_after'], len(snap.conversations))
        self.assertEqual(report['corpus_after'], 4)

    def test_no_new_ids_still_commits_existing_changes(self):
        self.seed_openai()
        export = {'a': make_conv('a', ['alpha one', 'alpha two', 'more'],
                                 provider='chatgpt')}
        rc, report = self.run_main(export)
        self.assertEqual(rc, 0)
        snap = self.snapshot()
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')),
                         ['alpha one', 'alpha two', 'more'])
        self.assertEqual(report['corpus_after'], len(snap.conversations))

    def test_absent_ids_described_as_absent_not_deletions(self):
        self.seed_openai()
        _, report = self.run_main(self.export_extended_plus_new())
        absent_counts = [v for k, v in report.items() if 'absent' in k
                         and isinstance(v, int)]
        self.assertIn(1, absent_counts,
                      "report must count 'b' as absent from the export")
        self.assertFalse([k for k in report
                          if 'delet' in k and 'absent' not in k],
                         'absence must not be reported as confirmed deletion')

    def test_dry_run_projects_without_publishing(self):
        seeded = self.seed_openai()
        rc, report = self.run_main(self.export_extended_plus_new(),
                                   dry_run=True)
        self.assertEqual(rc, 0)
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['projected_after'], 4)
        self.assertEqual(self.snapshot().token, seeded.token,
                         'dry run must not publish a new generation')

    def test_parse_export_rejects_conflicting_shard_duplicates(self):
        export_dir = os.path.join(self.data_dir, 'export')
        os.makedirs(export_dir)
        for shard in ('conversations-000.json', 'conversations-001.json'):
            with open(os.path.join(export_dir, shard), 'w') as f:
                f.write('[]')

        def conflicting_parser(path):
            text = ('one way' if path.endswith('conversations-000.json')
                    else 'ANOTHER way')
            return [make_conv('dup', [text], provider='chatgpt')]

        with mock.patch.object(openai_mod, 'parse_chatgpt_export',
                               conflicting_parser):
            with self.assertRaises(ValueError):
                openai_mod.parse_export(export_dir)


class TestSweepAdapter(AdapterTestCase):
    """sweep.run must deliver existing-ID candidates to reconciliation and
    retain records absent from every source."""

    def test_run_reconciles_extended_id_and_retains_other_provider(self):
        self.seed()
        texts = ['alpha one', 'alpha two', 'alpha three']
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sweep, 'DATA_DIR', self.data_dir))
            stack.enter_context(mock.patch.object(
                ni, 'DATA_DIR', self.data_dir))
            stack.enter_context(mock.patch.object(
                sweep, 'source_claude', lambda: [make_conv('a', texts)]))
            for name in ('source_grok', 'source_gemini', 'source_abacus'):
                stack.enter_context(mock.patch.object(
                    sweep, name, lambda: []))
            stack.enter_context(mock.patch(
                'core.embedder.Embedder', FakeEmbedder))
            stack.enter_context(patched_graph())
            self.quiet(stack)
            result = sweep.run(dry_run=False)

        snap = self.snapshot()
        self.assertEqual(user_texts(self.conv_by_id(snap, 'a')), texts)
        b = self.conv_by_id(snap, 'b')
        self.assertEqual(b['provider'], 'chatgpt')
        self.assertEqual(user_texts(b), ['beta one'])
        self.assertEqual(result['corpus_after'], len(snap.conversations))
        self.assertEqual(result['corpus_after'], 2)


if __name__ == '__main__':
    unittest.main()
