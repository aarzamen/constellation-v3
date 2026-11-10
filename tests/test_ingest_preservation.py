"""Regression tests for reconciliation + shared ingest pipeline.

Contract under test (docs/superpowers/plans/2026-09-07-index-preservation.md,
Task 2): core.reconcile.reconcile_conversations and
core.ingest.ingest_conversations. Imports are red until implemented.

All assertions check the committed snapshot's records and vectors, never the
incoming source dicts. Notes are checked by synthetic bytes only.
"""

import contextlib
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ingest import ingest_conversations  # noqa: E402
from core.reconcile import reconcile_conversations  # noqa: E402
from core.storage import load_snapshot, publish_generation  # noqa: E402

NOTES_BYTES = b'{"synthetic-note": "do-not-touch"}'


def text_vector(text):
    """Deterministic unit vector (dim=3) derived from the text alone."""
    h = hashlib.sha256(text.encode('utf-8')).digest()
    v = np.array([int.from_bytes(h[i:i + 4], 'big') for i in (0, 4, 8)],
                 dtype=np.float64) + 1.0
    return v / np.linalg.norm(v)


def expected_conv_vector(user_texts, name='untitled'):
    """Mirror embed_conversations: mean of per-message vectors, L2-normalized."""
    texts = user_texts or [name]
    mean = np.mean([text_vector(t) for t in texts], axis=0)
    return mean / np.linalg.norm(mean)


class FakeEmbedder:
    dim = 3

    def __init__(self, model='fake-model', revision='r1'):
        self.embedding_identity = {
            'model': model, 'revision': revision, 'policy': 'user-mean-v1',
        }
        self.embedded_texts = []

    def embed(self, texts, progress_callback=None):
        self.embedded_texts.extend(texts)
        return np.array([text_vector(t) for t in texts])


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts, progress_callback=None):
        raise RuntimeError('simulated embedding failure')


def make_conv(cid, texts, provider='claude', name=None, created_at=None,
              **extra):
    messages = []
    for i, t in enumerate(texts):
        messages.append({'role': 'user', 'text': t})
        messages.append({'role': 'assistant', 'text': f're: {t}'})
        _ = i
    conv = {
        'id': cid,
        'name': name or f'Conversation {cid}',
        'created_at': created_at or '2025-06-01T00:00:00Z',
        'provider': provider,
        'messages': messages,
        'user_messages': list(texts),
    }
    conv.update(extra)
    return conv


def fake_build_clusters(embeddings, k_override=None):
    return {
        'k': 1,
        'labels': np.zeros(len(embeddings), dtype=int),
        'centroids': np.array([embeddings.mean(axis=0)]),
    }


def fake_build_edges(embeddings, conversations, **kwargs):
    return []


def fake_build_graph_data(conversations, embeddings, cluster_info, edges):
    """Minimal but VALID graph: one node per conversation ID, one cluster."""
    nodes = [{'id': c['id'], 'name': c['name'], 'cluster': 0}
             for c in conversations]
    return {
        'nodes': nodes,
        'edges': edges,
        'clusters': [{'id': 0, 'label': 'synthetic', 'color': '#7957d9',
                      'count': len(conversations)}],
        'timeline': [],
        'stats': {'totalConversations': len(conversations)},
    }


@contextlib.contextmanager
def patched_graph(build_graph_data=fake_build_graph_data):
    with mock.patch.multiple('core.indexer',
                             build_clusters=fake_build_clusters,
                             build_edges=fake_build_edges,
                             build_graph_data=build_graph_data):
        yield


class IngestTestCase(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        with open(os.path.join(self.data_dir, 'notes.json'), 'wb') as f:
            f.write(NOTES_BYTES)

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def ingest(self, incoming, embedder=None, data_dir=None, **kwargs):
        embedder = embedder or FakeEmbedder()
        with patched_graph():
            result = ingest_conversations(data_dir or self.data_dir,
                                          incoming, embedder, **kwargs)
        return result, embedder

    def seed(self, convs=None):
        convs = convs if convs is not None else [
            make_conv('a', ['alpha one', 'alpha two']),
            make_conv('b', ['beta one'], provider='chatgpt'),
        ]
        result, _ = self.ingest(convs)
        return result.snapshot

    def snapshot(self):
        return load_snapshot(self.data_dir)

    def conv_by_id(self, snap, cid):
        for i, c in enumerate(snap.conversations):
            if c['id'] == cid:
                return i, c
        self.fail(f'conversation {cid} missing from snapshot')

    def assert_vector(self, snap, cid, user_texts, name='untitled'):
        idx, _ = self.conv_by_id(snap, cid)
        np.testing.assert_allclose(
            snap.embeddings[idx], expected_conv_vector(user_texts, name),
            atol=1e-9)

    def notes_bytes(self):
        with open(os.path.join(self.data_dir, 'notes.json'), 'rb') as f:
            return f.read()


class TestReconcilePure(IngestTestCase):
    """Direct reconcile_conversations contract, no storage involved."""

    def test_no_input_mutation_and_report_counts(self):
        existing = [make_conv('a', ['alpha one'])]
        incoming = [make_conv('a', ['alpha one', 'alpha two']),
                    make_conv('c', ['gamma'])]
        existing_copy = copy.deepcopy(existing)
        incoming_copy = copy.deepcopy(incoming)

        convs, report = reconcile_conversations(existing, incoming)

        self.assertEqual(existing, existing_copy)
        self.assertEqual(incoming, incoming_copy)
        self.assertEqual(report['new'], 1)
        self.assertEqual(report['extended'], 1)
        self.assertEqual(report['revisions'], 0)
        self.assertEqual({c['id'] for c in convs}, {'a', 'c'})

    def test_legacy_missing_provider_defaults_claude(self):
        existing = [make_conv('a', ['alpha one'])]
        del existing[0]['provider']
        incoming = [make_conv('a', ['alpha one'])]
        convs, report = reconcile_conversations(existing, incoming)
        _, rec = None, [c for c in convs if c['id'] == 'a'][0]
        self.assertEqual(rec['provider'], 'claude')
        self.assertEqual(report['unchanged'] + report['updated'], 1)

    def test_empty_id_rejected(self):
        with self.assertRaises(ValueError):
            reconcile_conversations([], [make_conv('', ['x'])])

    def test_user_messages_rebuilt_from_canonical_messages(self):
        incoming = [make_conv('a', ['alpha one'])]
        incoming[0]['user_messages'] = ['stale derived text']
        convs, _ = reconcile_conversations([], incoming)
        self.assertEqual(convs[0]['user_messages'], ['alpha one'])


class TestPreservationAndCache(IngestTestCase):

    def test_result_shape_and_tuple_unpack(self):
        result, _ = self.ingest([make_conv('a', ['alpha one'])])
        before, after = result  # nightly compatibility
        self.assertEqual((before, after), (result.before, result.after))
        self.assertEqual((before, after), (0, 1))
        self.assertEqual(result.report['new'], 1)
        # snapshot is the committed snapshot, matching what a reload sees
        self.assertEqual(result.snapshot.token, self.snapshot().token)

    def test_omitted_provider_retained_even_on_forced_rebuild(self):
        self.seed()
        result, embedder = self.ingest([make_conv('a', ['alpha one',
                                                        'alpha two'])],
                                       force_reembed=True)
        snap = result.snapshot
        _, b = self.conv_by_id(snap, 'b')
        self.assertEqual(b['provider'], 'chatgpt')
        # forced reembed includes all retained records
        self.assertIn('beta one', embedder.embedded_texts)
        self.assert_vector(snap, 'b', ['beta one'])
        self.assert_vector(snap, 'a', ['alpha one', 'alpha two'])

    def test_extended_history_same_id_reembedded(self):
        self.seed()
        texts = ['alpha one', 'alpha two', 'alpha three']
        result, embedder = self.ingest([make_conv('a', texts)])
        _, a = self.conv_by_id(result.snapshot, 'a')
        self.assertEqual([m['text'] for m in a['messages']
                          if m['role'] == 'user'], texts)
        self.assertEqual(result.report['extended'], 1)
        self.assertIn('alpha three', embedder.embedded_texts)
        self.assert_vector(result.snapshot, 'a', texts)

    def test_identical_history_new_message_ids_not_reembedded(self):
        self.seed()
        incoming = make_conv('a', ['alpha one', 'alpha two'])
        for i, m in enumerate(incoming['messages']):
            m['id'] = f'unstable-{i}'
            m['created_at'] = '2026-01-01T00:00:00Z'
        result, embedder = self.ingest([incoming])
        self.assertEqual(embedder.embedded_texts, [])
        self.assert_vector(result.snapshot, 'a', ['alpha one', 'alpha two'])

    def test_shorter_incoming_prefix_cannot_truncate(self):
        self.seed()
        result, embedder = self.ingest([make_conv('a', ['alpha one'])])
        _, a = self.conv_by_id(result.snapshot, 'a')
        self.assertEqual([m['text'] for m in a['messages']
                          if m['role'] == 'user'],
                         ['alpha one', 'alpha two'])
        self.assertEqual(embedder.embedded_texts, [])
        self.assert_vector(result.snapshot, 'a', ['alpha one', 'alpha two'])

    def test_metadata_only_update_enriches_without_reembed(self):
        self.seed()
        incoming = make_conv('a', ['alpha one', 'alpha two'],
                             model='claude-opus-4')
        result, embedder = self.ingest([incoming])
        _, a = self.conv_by_id(result.snapshot, 'a')
        self.assertEqual(a['model'], 'claude-opus-4')
        self.assertEqual(a['provider'], 'claude')  # existing fields kept
        self.assertEqual(embedder.embedded_texts, [])
        self.assertEqual(result.report['updated'], 1)


class TestDivergenceAndConflicts(IngestTestCase):

    def test_divergence_preserves_original_and_adds_revision(self):
        self.seed()
        diverged = make_conv('a', ['alpha one', 'alpha CHANGED'])
        result, _ = self.ingest([diverged])
        snap = result.snapshot
        _, a = self.conv_by_id(snap, 'a')
        self.assertEqual([m['text'] for m in a['messages']
                          if m['role'] == 'user'],
                         ['alpha one', 'alpha two'])
        revisions = [c for c in snap.conversations
                     if c.get('revision_of') == 'a']
        self.assertEqual(len(revisions), 1)
        rev = revisions[0]
        self.assertNotEqual(rev['id'], 'a')
        self.assertEqual([m['text'] for m in rev['messages']
                          if m['role'] == 'user'],
                         ['alpha one', 'alpha CHANGED'])
        self.assert_vector(snap, rev['id'], ['alpha one', 'alpha CHANGED'])
        self.assertEqual(result.report['revisions'], 1)
        return rev['id']

    def test_repeated_divergence_idempotent_and_id_deterministic(self):
        rev_id = self.test_divergence_preserves_original_and_adds_revision()
        count_before = len(self.snapshot().conversations)
        result, embedder = self.ingest(
            [make_conv('a', ['alpha one', 'alpha CHANGED'])])
        snap = result.snapshot
        self.assertEqual(len(snap.conversations), count_before)
        self.assertEqual([c['id'] for c in snap.conversations
                          if c.get('revision_of') == 'a'], [rev_id])
        self.assertEqual(embedder.embedded_texts, [])
        # same divergence in an independent archive yields the same UUID5 id
        other_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(other_dir, 'notes.json'), 'wb') as f:
                f.write(NOTES_BYTES)
            self.ingest([make_conv('a', ['alpha one', 'alpha two']),
                         make_conv('b', ['beta one'], provider='chatgpt')],
                        data_dir=other_dir)
            r2, _ = self.ingest(
                [make_conv('a', ['alpha one', 'alpha CHANGED'])],
                data_dir=other_dir)
            self.assertEqual([c['id'] for c in r2.snapshot.conversations
                              if c.get('revision_of') == 'a'], [rev_id])
        finally:
            shutil.rmtree(other_dir, ignore_errors=True)

    def test_cross_provider_collision_rejected(self):
        seeded = self.seed()
        with self.assertRaises(ValueError):
            self.ingest([make_conv('a', ['alpha one', 'alpha two'],
                                   provider='chatgpt')])
        self.assertEqual(self.snapshot().token, seeded.token)

    def test_conflicting_duplicate_incoming_ids_rejected_not_last_wins(self):
        seeded = self.seed()
        with self.assertRaises(ValueError):
            self.ingest([make_conv('c', ['gamma one']),
                         make_conv('c', ['gamma OTHER'])])
        self.assertEqual(self.snapshot().token, seeded.token)
        self.assertEqual(self.notes_bytes(), NOTES_BYTES)


class TestEmbeddingIdentity(IngestTestCase):

    def test_model_change_same_dim_reembeds_everything(self):
        self.seed()
        result, embedder = self.ingest(
            [make_conv('a', ['alpha one', 'alpha two'])],
            embedder=FakeEmbedder(model='fake-model-2'))
        self.assertIn('alpha one', embedder.embedded_texts)
        self.assertIn('beta one', embedder.embedded_texts)
        self.assertEqual(result.snapshot.embedding_identity,
                         embedder.embedding_identity)
        self.assert_vector(result.snapshot, 'b', ['beta one'])

    def test_legacy_flat_index_has_unverified_identity_and_reembeds(self):
        # Pre-generation flat layout: no identity recorded anywhere.
        convs = [make_conv('a', ['alpha one']),
                 make_conv('b', ['beta one'], provider='chatgpt')]
        flat = [{k: c[k] for k in
                 ('id', 'name', 'created_at', 'provider', 'messages')}
                for c in convs]
        with open(os.path.join(self.data_dir, 'conversations.json'),
                  'w') as f:
            json.dump(flat, f)
        legacy_vecs = np.ones((2, 3)) / np.sqrt(3)
        np.save(os.path.join(self.data_dir, 'embeddings.npy'), legacy_vecs)
        with patched_graph():
            graph = fake_build_graph_data(convs, legacy_vecs, None, [])
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump(graph, f)
        with open(os.path.join(self.data_dir, 'clusters.json'), 'w') as f:
            json.dump({'k': 1, 'clusters': graph['clusters']}, f)

        result, embedder = self.ingest([make_conv('a', ['alpha one'])])
        # legacy vectors may not be reused: everything re-embedded
        self.assertIn('alpha one', embedder.embedded_texts)
        self.assertIn('beta one', embedder.embedded_texts)
        self.assert_vector(result.snapshot, 'a', ['alpha one'])
        self.assert_vector(result.snapshot, 'b', ['beta one'])


class TestConcurrencyAndFailure(IngestTestCase):

    def test_stale_cas_retries_and_retains_interleaved_writer(self):
        self.seed()
        interleaved = {'done': False}
        outer = self

        def interleaving_graph(conversations, embeddings, cluster_info, edges):
            if not interleaved['done']:
                interleaved['done'] = True
                snap = load_snapshot(outer.data_dir)
                z = make_conv('z', ['zeta one'])
                convs = list(snap.conversations) + [z]
                vecs = np.vstack([snap.embeddings,
                                  expected_conv_vector(['zeta one'])])
                publish_generation(
                    outer.data_dir, convs, vecs,
                    fake_build_graph_data(convs, vecs, None, []),
                    expected_generation=snap.token,
                    embedding_identity=snap.embedding_identity)
            return fake_build_graph_data(conversations, embeddings,
                                         cluster_info, edges)

        embedder = FakeEmbedder()
        with patched_graph(build_graph_data=interleaving_graph):
            result = ingest_conversations(self.data_dir,
                                          [make_conv('c', ['gamma one'])],
                                          embedder)
        snap = result.snapshot
        ids = {c['id'] for c in snap.conversations}
        # retry rebuilt against the fresh corpus: both writers survive
        self.assertLessEqual({'a', 'b', 'c', 'z'}, ids)
        self.assertEqual(snap.token, self.snapshot().token)
        self.assert_vector(snap, 'z', ['zeta one'])
        self.assert_vector(snap, 'c', ['gamma one'])

    def test_failed_embedding_leaves_index_and_notes_untouched(self):
        seeded = self.seed()
        with self.assertRaises(Exception):
            self.ingest([make_conv('c', ['gamma one'])],
                        embedder=FailingEmbedder())
        snap = self.snapshot()
        self.assertEqual(snap.token, seeded.token)
        self.assertEqual({c['id'] for c in snap.conversations}, {'a', 'b'})
        self.assertEqual(self.notes_bytes(), NOTES_BYTES)

    def test_failed_publish_leaves_index_and_notes_untouched(self):
        seeded = self.seed()

        def broken_graph(conversations, embeddings, cluster_info, edges):
            graph = fake_build_graph_data(conversations, embeddings,
                                          cluster_info, edges)
            graph['edges'] = [{'source': 'nope', 'target': 'also-nope',
                               'weight': 0.5}]
            return graph

        with self.assertRaises(Exception):
            with patched_graph(build_graph_data=broken_graph):
                ingest_conversations(self.data_dir,
                                     [make_conv('c', ['gamma one'])],
                                     FakeEmbedder())
        snap = self.snapshot()
        self.assertEqual(snap.token, seeded.token)
        self.assertEqual(self.notes_bytes(), NOTES_BYTES)


if __name__ == '__main__':
    unittest.main()
