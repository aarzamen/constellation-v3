"""Reader-side tests for generation-based index serving (Task 2, red first).

Contract under test (docs/superpowers/plans/2026-09-07-index-preservation.md):

- SearchEngine.load() captures one immutable state per operation; internal
  search/get/list/recent/stats never reread engine mutable fields mid-request.
- engine.get_snapshot() returns the currently loaded core.storage.IndexSnapshot
  (loading if needed).
- Reload keys off the published generation token, not flat-file mtimes.
- Query embedders are cached by snapshot embedding identity; an injected
  engine.embedder is honored only while its identity matches, never silently
  reused after a model change (even at the same dimension).
- Failed reloads leave previously held state intact and surface the error.
- REST graph serving and reclustering read from / publish to the committed
  generation via compare-and-swap.
- Notes migration cannot resurrect embedded notes from obsolete flat
  conversations.json once a generation pointer exists.

No real embedding model is imported: core.embedder.Embedder is patched with a
synthetic dim-8 implementation carrying identity.
"""

import email.message
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notes import load_notes
from core.storage import (
    IndexCorruptError,
    IndexSnapshot,
    StaleGenerationError,
    load_snapshot,
    publish_generation,
)

DIM = 8
POLICY = 'user-messages-v1'


def identity_for(model, revision='r1'):
    return {'model': model, 'revision': revision, 'policy': POLICY}


def det_vec(model, text):
    """Deterministic unit vector: same (model, text) -> same embedding."""
    seed = int.from_bytes(
        hashlib.sha256(f'{model}|{text}'.encode()).digest()[:8], 'big')
    vec = np.random.default_rng(seed).normal(size=DIM).astype(np.float32)
    return vec / np.linalg.norm(vec)


class SyntheticEmbedder:
    """Stands in for core.embedder.Embedder. Records construction and use."""

    instances = []

    def __init__(self, model_name='model-a', revision=None, **kwargs):
        self.model_name = model_name
        self.revision = revision or 'r1'
        self.embedding_identity = identity_for(self.model_name, self.revision)
        self.queries = []
        SyntheticEmbedder.instances.append(self)

    def embed_query(self, text):
        self.queries.append(text)
        return det_vec(self.model_name, text)

    def embed(self, texts):
        return np.stack([det_vec(self.model_name, t) for t in texts])


def make_conversations(n, prefix='conv'):
    return [
        {
            'id': f'{prefix}-{i}',
            'name': f'Conversation {prefix} {i}',
            'created_at': f'2025-06-{i + 1:02d}T00:00:00Z',
            'provider': 'claude',
            'messages': [{'role': 'user', 'text': f'{prefix} message {i}'}],
        }
        for i in range(n)
    ]


def make_embeddings(n, seed=0):
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=(n, DIM)).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def make_graph_data(conversations):
    nodes = [{'id': c['id'], 'name': c['name'], 'cluster': 0,
              'clusterLabel': 'synthetic'} for c in conversations]
    edges = []
    if len(conversations) > 1:
        edges.append({'source': conversations[0]['id'],
                      'target': conversations[1]['id'], 'weight': 0.5})
    return {
        'nodes': nodes,
        'edges': edges,
        'clusters': [{'id': 0, 'label': 'synthetic', 'color': '#7957d9',
                      'count': len(conversations)}],
        'timeline': [],
        'stats': {'totalConversations': len(conversations)},
    }


class ReaderTestCase(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        SyntheticEmbedder.instances = []
        patcher = mock.patch('core.embedder.Embedder', SyntheticEmbedder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def publish(self, convs, seed=0, expected=None, model='model-a',
                chunk_embeddings=None, chunk_to_conv=None):
        return publish_generation(
            self.data_dir, convs, make_embeddings(len(convs), seed=seed),
            make_graph_data(convs),
            chunk_embeddings, chunk_to_conv,
            expected_generation=expected,
            embedding_identity=identity_for(model))

    def make_engine(self):
        from server.api import SearchEngine
        return SearchEngine(data_dir=self.data_dir)


class TestSnapshotReaders(ReaderTestCase):

    def test_generated_snapshot_readable_across_operations(self):
        convs = make_conversations(3)
        chunk_embs = make_embeddings(4, seed=7)
        gen = self.publish(convs, chunk_embeddings=chunk_embs,
                           chunk_to_conv=[0, 0, 1, 2])
        engine = self.make_engine()

        snap = engine.get_snapshot()
        self.assertIsInstance(snap, IndexSnapshot)
        self.assertEqual(snap.token, gen.token)

        listed = engine.list_conversations(limit=10)
        self.assertEqual(listed['total'], 3)
        self.assertEqual({c['id'] for c in listed['conversations']},
                         {c['id'] for c in convs})

        full = engine.get_conversation('conv-1')
        self.assertEqual(full['id'], 'conv-1')
        self.assertEqual(full['cluster_label'], 'synthetic')

        stats = engine.get_stats()
        self.assertEqual(stats['totalConversations'], 3)
        self.assertEqual(stats['embeddingDim'], DIM)

        recent = engine.list_recent_conversations(n=2)
        self.assertEqual(len(recent), 2)

        results = engine.search('message', top_k=3)
        self.assertTrue(results)
        self.assertTrue({r['id'] for r in results} <= {c['id'] for c in convs})

    def test_reload_switches_generation_despite_stable_flat_sentinel(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs, seed=0)

        # Decoy flat file with a frozen mtime: same conversation metadata as
        # both generations. Reload must key off the pointer, not this file.
        decoy = os.path.join(self.data_dir, 'conversations.json')
        with open(decoy, 'w') as f:
            json.dump(convs, f)
        os.utime(decoy, (1_000_000_000, 1_000_000_000))

        engine = self.make_engine()
        self.assertEqual(engine.get_snapshot().token, gen1.token)

        # Same conversations, new vectors; decoy mtime unchanged.
        gen2 = self.publish(convs, seed=42, expected=gen1.token)
        os.utime(decoy, (1_000_000_000, 1_000_000_000))

        engine.get_stats()
        self.assertEqual(engine.get_snapshot().token, gen2.token)

    def test_chunk_removal_clears_stale_chunk_state(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs, chunk_embeddings=make_embeddings(4, seed=5),
                            chunk_to_conv=[0, 0, 1, 2])
        engine = self.make_engine()
        results = engine.search('message', top_k=3)
        self.assertTrue(any(r['chunk_score'] != 0.0 for r in results))

        self.publish(convs, seed=1, expected=gen1.token)
        results = engine.search('message', top_k=3)
        self.assertTrue(results)
        self.assertTrue(all(r['chunk_score'] == 0.0 for r in results))
        snap = engine.get_snapshot()
        self.assertIsNone(snap.chunk_embeddings)
        self.assertIsNone(snap.chunk_to_conv)


class TestFailedLoads(ReaderTestCase):

    def corrupt_pointer(self):
        with open(os.path.join(self.data_dir, 'current.json'), 'w') as f:
            f.write('{not valid json')

    def test_first_load_invalid_fails_clearly(self):
        self.publish(make_conversations(2))
        self.corrupt_pointer()
        engine = self.make_engine()
        with self.assertRaises(IndexCorruptError):
            engine.get_stats()

    def test_invalid_reload_keeps_held_state_and_surfaces_error(self):
        gen = self.publish(make_conversations(3))
        engine = self.make_engine()
        held = engine.get_snapshot()
        held_ids = [c['id'] for c in held.conversations]

        self.corrupt_pointer()
        with self.assertRaises(IndexCorruptError):
            engine.get_stats()

        # The previously held snapshot object was not damaged by the failure.
        self.assertEqual(held.token, gen.token)
        self.assertEqual([c['id'] for c in held.conversations], held_ids)


class TestInFlightIsolation(ReaderTestCase):

    def test_inflight_search_pinned_to_original_generation(self):
        old_convs = make_conversations(3, prefix='old')
        gen1 = self.publish(old_convs)
        engine = self.make_engine()

        started = threading.Event()
        release = threading.Event()

        class BlockingEmbedder(SyntheticEmbedder):
            def embed_query(self, text):
                started.set()
                assert release.wait(timeout=10), 'never released'
                return super().embed_query(text)

        results = []
        errors = []

        def do_search():
            try:
                results.extend(engine.search('message', top_k=5))
            except Exception as e:  # surfaced in the assertion below
                errors.append(e)

        def do_swap():
            self.publish(make_conversations(4, prefix='new'),
                         seed=9, expected=gen1.token)
            engine.load()

        searcher = threading.Thread(target=do_search)
        try:
            with mock.patch('core.embedder.Embedder', BlockingEmbedder):
                searcher.start()
                self.assertTrue(started.wait(timeout=10),
                                'search never reached query embedding')
                swapper = threading.Thread(target=do_swap)
                swapper.start()
                swapper.join(timeout=10)
                self.assertFalse(swapper.is_alive(),
                                 'reload blocked behind in-flight search')
        finally:
            release.set()
            searcher.join(timeout=10)
        self.assertFalse(searcher.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(results)
        old_ids = {c['id'] for c in old_convs}
        self.assertTrue({r['id'] for r in results} <= old_ids,
                        f'in-flight search leaked new generation: {results}')

        # A fresh request after release sees the new generation.
        after = engine.search('message', top_k=5)
        self.assertTrue({r['id'] for r in after} <= {f'new-{i}'
                                                     for i in range(4)})


class TestEmbedderIdentity(ReaderTestCase):

    def test_same_dim_model_change_selects_new_query_embedder(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs, model='model-a')
        engine = self.make_engine()
        injected = SyntheticEmbedder(model_name='model-a')
        engine.embedder = injected

        engine.search('first query', top_k=2)
        self.assertEqual(injected.queries, ['first query'],
                         'matching injected embedder must be used')

        # Same dimension, different model identity.
        self.publish(convs, seed=3, expected=gen1.token, model='model-b')
        engine.search('second query', top_k=2)

        self.assertNotIn('second query', injected.queries,
                         'stale model-a embedder silently reused')
        used_b = [e for e in SyntheticEmbedder.instances
                  if e.model_name == 'model-b' and 'second query' in e.queries]
        self.assertTrue(used_b,
                        'no model-b embedder answered the post-switch query')


def fake_http_get(path):
    """Drive ConstellationHandler.do_GET directly — no socket, no server."""
    from server.http_server import ConstellationHandler
    handler = ConstellationHandler.__new__(ConstellationHandler)
    handler.command = 'GET'
    handler.path = path
    handler.request_version = 'HTTP/1.1'
    handler.requestline = f'GET {path} HTTP/1.1'
    handler.client_address = ('127.0.0.1', 0)
    handler.headers = email.message.Message()
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.do_GET()
    raw = handler.wfile.getvalue()
    header, _, body = raw.partition(b'\r\n\r\n')
    status = int(header.split()[1])
    return status, (json.loads(body) if body else None)


class RestTestCase(ReaderTestCase):

    def setUp(self):
        super().setUp()
        import server.http_server as http_server
        self.http_server = http_server
        engine = self.make_engine()
        for target in (mock.patch.object(http_server, 'search_engine', engine),
                       mock.patch.object(http_server, 'DATA_DIR',
                                         self.data_dir)):
            target.start()
            self.addCleanup(target.stop)


class TestGraphRest(RestTestCase):

    def test_graph_route_serves_committed_generation(self):
        convs = make_conversations(3)
        gen = self.publish(convs)
        # Stale flat graph at the data root must not be what gets served.
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump({'nodes': [], 'edges': [], 'clusters': [],
                       'stale': True}, f)

        status, body = fake_http_get('/data/graph_data.json')
        self.assertEqual(status, 200)
        self.assertEqual(body, gen.graph_data)


class TestRecluster(RestTestCase):

    def stub_indexer(self, build_graph):
        import core.indexer as indexer
        for name, fn in (('build_clusters', lambda *a, **k: {'stub': True}),
                         ('build_edges', lambda *a, **k: []),
                         ('build_graph_data', build_graph)):
            p = mock.patch.object(indexer, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def test_recluster_publishes_via_cas_from_snapshot(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs)
        new_graph = make_graph_data(convs)
        new_graph['stats']['reclustered'] = True
        self.stub_indexer(lambda *a, **k: new_graph)

        status, body = fake_http_get('/api/recluster?k=2')
        self.assertEqual(status, 200)
        self.assertEqual(body, new_graph)

        committed = load_snapshot(self.data_dir)
        self.assertNotEqual(committed.token, gen1.token)
        self.assertEqual(committed.graph_data, new_graph)
        # Same corpus and vectors carried into the new generation.
        self.assertEqual([c['id'] for c in committed.conversations],
                         [c['id'] for c in convs])
        # Old generation retained for held readers.
        self.assertTrue(os.path.isdir(gen1.directory))

    def test_publish_race_during_graph_build_rejects_stale_recluster(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs)
        raced = {}

        def racing_build(*a, **k):
            # Another writer commits while the graph is being rebuilt.
            raced['gen'] = self.publish(make_conversations(4, prefix='raced'),
                                        seed=6, expected=gen1.token)
            return make_graph_data(convs)

        self.stub_indexer(racing_build)
        status, body = fake_http_get('/api/recluster?k=2')
        self.assertTrue(status >= 400 or (isinstance(body, dict)
                                          and 'error' in body),
                        f'stale recluster not rejected: {status} {body}')

        committed = load_snapshot(self.data_dir)
        self.assertEqual(committed.token, raced['gen'].token,
                         'stale recluster clobbered the racing publish')


class TestNotesGuard(ReaderTestCase):

    def test_stale_flat_embedded_notes_not_resurrected(self):
        convs = make_conversations(3)
        self.publish(convs)

        # Obsolete flat corpus at the root with embedded notes; no sidecar.
        stale = json.loads(json.dumps(convs))
        stale[0]['notes'] = [{'text': 'stale flat note',
                              'created_at': '2024-01-01T00:00:00Z'}]
        with open(os.path.join(self.data_dir, 'conversations.json'), 'w') as f:
            json.dump(stale, f)
        self.assertFalse(
            os.path.exists(os.path.join(self.data_dir, 'notes.json')))

        engine = self.make_engine()
        full = engine.get_conversation('conv-0')
        self.assertEqual(full['notes'], [])

        loaded = load_notes(self.data_dir)
        texts = [n.get('text') for notes in loaded.values() for n in notes]
        self.assertNotIn('stale flat note', texts,
                         'legacy migration resurrected obsolete flat notes')


if __name__ == '__main__':
    unittest.main()
