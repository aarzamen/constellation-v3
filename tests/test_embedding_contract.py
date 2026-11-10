"""Embedding identity / provenance contract tests (red-first).

Holes under test, per independent review of Task 2
(docs/superpowers/plans/2026-09-07-index-preservation.md):

1. core.embedder.Embedder drops model/policy provenance when weights cannot
   be pinned to a commit (identity must stay truthful, only unverified).
2. Ingest with a None-revision identity must never reuse cached vectors, yet
   must still publish the truthful model/policy (cache eligibility is
   separate from provenance).
3. A verified chunkless snapshot must be backfilled: new ingests re-embed
   retained records and publish complete chunk coverage, not discard chunks.
4. A native incoming record squatting on a minted revision id without
   revision_of provenance must be rejected in both batch orders.
5. Real graph builders must handle 1- and 2-record corpora with finite
   coordinates.
6-8. SearchEngine must never silently answer semantic queries with a model
   other than the one the index was built with; the legacy MiniLM assumption
   applies only to true flat legacy layouts.

No real models: sentence_transformers is faked via sys.modules for (1) and
core.embedder.Embedder is patched with recording stubs for (6-8). Synthetic
tempfile archives only.
"""

import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path[:0] = [ROOT, TESTS_DIR]

from core.ingest import ingest_conversations  # noqa: E402
from core.reconcile import reconcile_conversations  # noqa: E402
from core.storage import publish_generation  # noqa: E402
from server.api import LEGACY_DEFAULT_MODEL  # noqa: E402
from test_ingest_preservation import (  # noqa: E402
    FakeEmbedder,
    IngestTestCase,
    expected_conv_vector,
    fake_build_graph_data,
    make_conv,
    text_vector,
)

POLICY = 'user-messages-v1'
EMBEDDER_PATH = os.path.join(ROOT, 'core', 'embedder.py')


def identity_for(model, revision='r1'):
    return {'model': model, 'revision': revision, 'policy': POLICY}


def conv_matrix(convs):
    return np.array([expected_conv_vector(c['user_messages'], c['name'])
                     for c in convs])


# ---------------------------------------------------------------------------
# 1. Real Embedder class, faked sentence_transformers: unverified weights
#    must keep model/policy provenance (revision None), not erase identity.

class TestEmbedderIdentityRetention(unittest.TestCase):

    def _load_real_embedder_module(self):
        """Load core/embedder.py fresh from source so a runner-level patch of
        core.embedder.Embedder cannot mask the real constructor."""
        fake = types.ModuleType('sentence_transformers')

        class FakeSentenceTransformer:
            def __init__(self, model_name, revision=None):
                self.model_name = model_name
                self.revision = revision

            def get_sentence_embedding_dimension(self):
                return 3

            def __getitem__(self, idx):
                # Local path weights: no resolvable commit hash.
                return types.SimpleNamespace(auto_model=types.SimpleNamespace(
                    config=types.SimpleNamespace(_commit_hash=None)))

            def encode(self, texts, **kwargs):
                return np.stack([text_vector(t) for t in texts])

        fake.SentenceTransformer = FakeSentenceTransformer
        spec = importlib.util.spec_from_file_location(
            'embedder_contract_under_test', EMBEDDER_PATH)
        mod = importlib.util.module_from_spec(spec)
        # Keep the fake active through Embedder.__init__'s lazy import, not
        # just module load, so the real model loader is never reached.
        patcher = mock.patch.dict(sys.modules, {'sentence_transformers': fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        spec.loader.exec_module(mod)
        return mod

    def test_unverified_local_model_keeps_model_and_policy(self):
        mod = self._load_real_embedder_module()
        emb = mod.Embedder('custom-local-model')
        self.assertEqual(emb.dim, 3)
        self.assertEqual(emb.embedding_identity,
                         {'model': 'custom-local-model',
                          'revision': None,
                          'policy': POLICY})


# ---------------------------------------------------------------------------
# 2. None-revision identity: never cache-eligible, but provenance published.

class TestUnverifiedIdentityIngest(IngestTestCase):

    def test_missing_revision_reembeds_but_publishes_truthful_identity(self):
        convs = [make_conv('a', ['alpha one'])]
        _, first = self.ingest(convs, embedder=FakeEmbedder(revision=None))
        self.assertIn('alpha one', first.embedded_texts)

        # Exact same ingest again: unverified vectors may never be reused.
        result, second = self.ingest(convs,
                                     embedder=FakeEmbedder(revision=None))
        self.assertIn('alpha one', second.embedded_texts,
                      'cached vectors reused under an unverifiable identity')

        identity = result.snapshot.embedding_identity
        self.assertIsNotNone(identity,
                             'model/policy provenance replaced with None')
        self.assertEqual(identity.get('model'), 'fake-model')
        self.assertEqual(identity.get('policy'), 'user-mean-v1')
        self.assertIsNone(identity.get('revision'))
        stats = result.snapshot.graph_data.get('stats', {})
        self.assertEqual(stats.get('embeddingModel'), 'fake-model')


# ---------------------------------------------------------------------------
# 3. Verified chunkless snapshot: shared ingest backfills full chunk coverage.

class TestChunklessSnapshotBackfill(IngestTestCase):

    def test_new_conversation_backfills_chunks_for_retained_records(self):
        embedder = FakeEmbedder()
        seed = [make_conv('a', ['alpha one', 'alpha two'])]
        publish_generation(
            self.data_dir, seed, conv_matrix(seed),
            fake_build_graph_data(seed, None, None, []),
            expected_generation=None,
            embedding_identity=embedder.embedding_identity)

        result, embedder = self.ingest([make_conv('c', ['gamma one'])],
                                       embedder=FakeEmbedder())
        snap = result.snapshot
        self.assertIsNotNone(snap.chunk_embeddings,
                             'new chunks discarded instead of backfilled')
        self.assertEqual(len(snap.chunk_to_conv), 3)  # 2 retained + 1 new
        self.assertEqual({int(i) for i in snap.chunk_to_conv},
                         set(range(len(snap.conversations))),
                         'chunk coverage incomplete for some records')
        self.assertIn('alpha one', embedder.embedded_texts,
                      'retained chunkless record was not re-embedded')


# ---------------------------------------------------------------------------
# 4. Provenance-less native record on a minted revision id: reject, both
#    orders, and the original archive stays idempotently reimportable.

class TestRevisionIdSquatting(unittest.TestCase):

    def setUp(self):
        self.a_orig = make_conv('a', ['alpha one', 'alpha two'])
        self.a_div = make_conv('a', ['alpha one', 'alpha CHANGED'])
        corpus, _ = reconcile_conversations([], [self.a_orig])
        self.corpus, _ = reconcile_conversations(corpus, [self.a_div])
        rev = [c for c in self.corpus if c.get('revision_of') == 'a']
        self.assertEqual(len(rev), 1)
        # Native record reusing R's id, extending its history, no provenance.
        self.r_native = make_conv(rev[0]['id'],
                                  ['alpha one', 'alpha CHANGED', 'alpha MORE'])

    def test_rejected_in_both_batch_orders(self):
        for batch in ([self.a_div, self.r_native],
                      [self.r_native, self.a_div]):
            with self.assertRaises(ValueError, msg=f'order {batch[0]["id"]}'):
                reconcile_conversations(self.corpus, batch)

    def test_original_archive_still_idempotently_reimportable(self):
        convs, report = reconcile_conversations(self.corpus, [self.a_orig])
        self.assertEqual([c['id'] for c in convs],
                         [c['id'] for c in self.corpus])
        self.assertEqual(report['unchanged'], 1)


# ---------------------------------------------------------------------------
# 5. Real graph builders on tiny corpora (no graph monkeypatching).

class TestRealGraphSmallCorpus(IngestTestCase):

    def assert_finite_nodes(self, graph, n):
        nodes = graph['nodes']
        self.assertEqual(len(nodes), n)
        for node in nodes:
            for key in ('x', 'y', 'z', 'tx', 'ty', 'tz'):
                self.assertTrue(math.isfinite(node[key]),
                                f'node {node["id"]} {key}={node[key]!r}')

    def test_singleton_ingest_succeeds_with_finite_coordinates(self):
        result = ingest_conversations(self.data_dir,
                                      [make_conv('a', ['alpha one'])],
                                      FakeEmbedder())
        graph = result.snapshot.graph_data
        self.assert_finite_nodes(graph, 1)
        self.assertEqual(graph['edges'], [])

    def test_two_record_ingest_has_finite_coordinates(self):
        result = ingest_conversations(
            self.data_dir,
            [make_conv('a', ['alpha one']), make_conv('b', ['beta one'])],
            FakeEmbedder())
        self.assert_finite_nodes(result.snapshot.graph_data, 2)


# ---------------------------------------------------------------------------
# 6-8. Reader-side query model selection.

class QueryEmbedderStub:
    """Stands in for core.embedder.Embedder; records construction and use."""

    instances = []

    def __init__(self, model_name=LEGACY_DEFAULT_MODEL, revision=None, **kw):
        self.model_name = model_name
        self.dim = 3
        self.embedding_identity = identity_for(model_name, revision or 'r1')
        self.queries = []
        QueryEmbedderStub.instances.append(self)

    def embed_query(self, query):
        self.queries.append(query)
        return text_vector(query).reshape(1, -1)


class NoIdentityEmbedderStub(QueryEmbedderStub):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.embedding_identity = None


class ReaderContractCase(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        QueryEmbedderStub.instances = []
        patcher = mock.patch('core.embedder.Embedder', QueryEmbedderStub)
        patcher.start()
        self.addCleanup(patcher.stop)

    def convs(self):
        return [make_conv('a', ['alpha one']), make_conv('b', ['beta one'])]

    def publish(self, convs, identity, expected=None):
        return publish_generation(
            self.data_dir, convs, conv_matrix(convs),
            fake_build_graph_data(convs, None, None, []),
            expected_generation=expected, embedding_identity=identity)

    def write_flat(self, convs):
        graph = fake_build_graph_data(convs, None, None, [])
        with open(os.path.join(self.data_dir, 'conversations.json'), 'w') as f:
            json.dump(convs, f)
        np.save(os.path.join(self.data_dir, 'embeddings.npy'),
                conv_matrix(convs))
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump(graph, f)
        with open(os.path.join(self.data_dir, 'clusters.json'), 'w') as f:
            json.dump({'k': 1, 'clusters': graph['clusters']}, f)

    def engine(self):
        from server.api import SearchEngine
        return SearchEngine(data_dir=self.data_dir)

    def answered_by(self, query):
        return [e for e in QueryEmbedderStub.instances if query in e.queries]


class TestStoredIdentitySelection(ReaderContractCase):

    def test_generated_unverified_custom_identity_reported_and_rejected(self):
        self.publish(self.convs(),
                     {'model': 'custom-model', 'revision': None,
                      'policy': POLICY})
        engine = self.engine()
        stats = engine.get_stats()
        self.assertEqual(stats['embeddingModel'], 'custom-model')
        self.assertFalse(stats['identityVerified'])
        with self.assertRaises(ValueError):
            engine.search('alpha one', top_k=2)
        self.assertFalse(self.answered_by('alpha one'),
                         'query embedded despite unverifiable index identity')
        self.assertFalse(any(e.model_name == LEGACY_DEFAULT_MODEL
                             for e in QueryEmbedderStub.instances),
                         'silently fell back to the default MiniLM model')

    def test_generated_none_identity_rejects_semantic_search(self):
        self.publish(self.convs(), None)
        engine = self.engine()
        with self.assertRaises(ValueError):
            engine.search('alpha one', top_k=2)
        self.assertFalse(self.answered_by('alpha one'),
                         'generation without identity treated as legacy flat')

    def test_legacy_flat_none_identity_uses_explicit_default_assumption(self):
        self.write_flat(self.convs())
        engine = self.engine()
        stats = engine.get_stats()
        self.assertEqual(stats['embeddingModel'], LEGACY_DEFAULT_MODEL)
        self.assertTrue(stats['identityLegacyAssumed'])
        self.assertTrue(engine.search('alpha one', top_k=2))
        answered = self.answered_by('alpha one')
        self.assertTrue(answered)
        self.assertTrue(all(e.model_name == LEGACY_DEFAULT_MODEL
                            for e in answered))


class TestUnverifiableQueryModel(ReaderContractCase):

    def test_query_model_reporting_no_identity_is_rejected(self):
        self.publish(self.convs(), identity_for('model-a'))
        with mock.patch('core.embedder.Embedder', NoIdentityEmbedderStub):
            engine = self.engine()
            with self.assertRaises(ValueError):
                engine.search('alpha one', top_k=2)
        self.assertFalse(self.answered_by('alpha one'),
                         'unverifiable query model accepted against a '
                         'verified index identity')


class TestVerifiedToLegacyTransition(ReaderContractCase):

    def test_pointer_removal_does_not_reuse_model_a_as_legacy_default(self):
        convs = self.convs()
        self.publish(convs, identity_for('model-a'))
        engine = self.engine()
        engine.search('first query', top_k=2)
        model_a = self.answered_by('first query')
        self.assertTrue(model_a)
        self.assertEqual(model_a[0].model_name, 'model-a')

        # Transition to a legacy flat layout: pointer gone, flat artifacts in.
        os.remove(os.path.join(self.data_dir, 'current.json'))
        self.write_flat(convs)

        engine.search('second query', top_k=2)
        self.assertNotIn('second query', model_a[0].queries,
                         'stale model-a embedder reused as the legacy '
                         'default assumption')
        answered = self.answered_by('second query')
        self.assertTrue(answered)
        self.assertTrue(all(e.model_name == LEGACY_DEFAULT_MODEL
                            for e in answered),
                        'legacy snapshot answered by a non-default model')


class TestVerifiedFlagDowngrade(ReaderContractCase):
    """core.ingest._cache_eligible refuses an identity carrying
    verified=False, but the reader ignores that flag: an injected embedder
    matches it by dict equality, and the embedder cache keys only on
    (model, revision, policy), so a same-key embedder warmed under the
    verified identity keeps answering after the downgrade."""

    def _downgraded_engine(self):
        """Fresh dir: warm a verified model-a engine with one successful
        search, then republish the identical identity marked verified=False."""
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        QueryEmbedderStub.instances = []
        convs = self.convs()
        snap = self.publish(convs, identity_for('model-a'))
        engine = self.engine()
        engine.search('warm query', top_k=2)
        self.assertTrue(self.answered_by('warm query'))
        false_identity = dict(identity_for('model-a'), verified=False)
        self.publish(convs, false_identity, expected=snap.token)
        return engine, false_identity

    def test_verified_false_rejected_on_injected_and_cached_paths(self):
        for path in ('injected', 'cached'):
            with self.subTest(path=path):
                engine, false_identity = self._downgraded_engine()
                if path == 'injected':
                    injected = QueryEmbedderStub('model-a')
                    injected.embedding_identity = false_identity
                    engine.embedder = injected
                else:
                    engine.embedder = None
                stats = engine.get_stats()
                self.assertFalse(stats['identityVerified'],
                                 'verified=False identity reported verified')
                with self.assertRaises(ValueError):
                    engine.search('downgraded query', top_k=2)
                self.assertFalse(
                    self.answered_by('downgraded query'),
                    f'{path} embedder answered under verified=False identity')


if __name__ == '__main__':
    unittest.main()
