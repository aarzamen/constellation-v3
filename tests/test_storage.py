"""Tests for the generation-based index storage primitive (core/storage.py).

Contract under test (docs/superpowers/plans/2026-09-07-index-preservation.md,
Task 1): immutable generations under data/generations/<id>/, one atomic
current.json pointer, compare-and-swap publication, fail-closed corruption
handling, and flat-legacy fallback only when no pointer exists.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.storage import (
    IndexCorruptError,
    StaleGenerationError,
    load_snapshot,
    publish_generation,
)

IDENTITY = {
    'model': 'synthetic',
    'revision': 'test-v1',
    'policy': 'user-messages-v1',
}

PUBLISH_ERRORS = (ValueError, IndexCorruptError)


def make_conversations(n):
    return [
        {
            'id': f'conv-{i}',
            'name': f'Conversation {i}',
            'created_at': f'2025-06-{i + 1:02d}T00:00:00Z',
            'provider': 'claude',
            'messages': [{'role': 'user', 'text': f'message {i}'}],
        }
        for i in range(n)
    ]


def make_embeddings(n, dim=8, seed=0):
    rng = np.random.default_rng(seed)
    vecs = rng.normal(size=(n, dim)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs


def make_graph_data(conversations):
    nodes = [{'id': c['id'], 'name': c['name'], 'cluster': 0}
             for c in conversations]
    edges = []
    if len(conversations) > 1:
        edges.append({
            'source': conversations[0]['id'],
            'target': conversations[1]['id'],
            'weight': 0.5,
        })
    return {
        'nodes': nodes,
        'edges': edges,
        'clusters': [{'id': 0, 'label': 'synthetic', 'color': '#7957d9',
                      'count': len(conversations)}],
        'timeline': [],
        'stats': {'totalConversations': len(conversations)},
    }


class StorageTestCase(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def make_corpus(self, n=3, seed=0):
        convs = make_conversations(n)
        return convs, make_embeddings(n, seed=seed), make_graph_data(convs)

    def publish_first(self, n=3, seed=0, **kwargs):
        convs, embs, graph = self.make_corpus(n, seed=seed)
        return publish_generation(
            self.data_dir, convs, embs, graph,
            expected_generation=None, embedding_identity=IDENTITY, **kwargs)

    def write_flat_legacy(self, n=3):
        """Complete flat-layout index at the data root (pre-generation era)."""
        convs, embs, graph = self.make_corpus(n)
        with open(os.path.join(self.data_dir, 'conversations.json'), 'w') as f:
            json.dump(convs, f)
        np.save(os.path.join(self.data_dir, 'embeddings.npy'), embs)
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump(graph, f)
        with open(os.path.join(self.data_dir, 'clusters.json'), 'w') as f:
            json.dump({'k': 1, 'clusters': graph['clusters']}, f)
        return convs, embs, graph


class TestPublishAndLoadRoundtrip(StorageTestCase):

    def test_load_snapshot_no_index_returns_none(self):
        self.assertIsNone(load_snapshot(self.data_dir))

    def test_publish_then_load_roundtrip(self):
        convs, embs, graph = self.make_corpus()
        chunk_embs = make_embeddings(5, seed=7)
        chunk_map = [0, 0, 1, 2, 2]
        published = publish_generation(
            self.data_dir, convs, embs, graph, chunk_embs, chunk_map,
            expected_generation=None, embedding_identity=IDENTITY)

        self.assertTrue(published.token)
        loaded = load_snapshot(self.data_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.token, published.token)
        self.assertEqual(loaded.conversations, convs)
        np.testing.assert_allclose(loaded.embeddings, embs)
        self.assertEqual(loaded.graph_data, graph)
        self.assertIsNotNone(loaded.clusters)
        np.testing.assert_allclose(loaded.chunk_embeddings, chunk_embs)
        self.assertEqual(list(loaded.chunk_to_conv), chunk_map)
        self.assertEqual(loaded.embedding_identity, IDENTITY)
        self.assertTrue(os.path.isdir(loaded.directory))
        self.assertIn(os.path.join('generations', ''),
                      loaded.directory + os.sep)

    def test_publish_without_chunks_loads_none_chunks(self):
        self.publish_first()
        loaded = load_snapshot(self.data_dir)
        self.assertIsNone(loaded.chunk_embeddings)
        self.assertIsNone(loaded.chunk_to_conv)

    def test_successive_generations_have_distinct_tokens(self):
        first = self.publish_first()
        convs, embs, graph = self.make_corpus(4, seed=1)
        second = publish_generation(
            self.data_dir, convs, embs, graph,
            expected_generation=first.token, embedding_identity=IDENTITY)
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(load_snapshot(self.data_dir).token, second.token)


class TestLegacyFallback(StorageTestCase):

    def test_full_flat_legacy_loads_when_no_pointer(self):
        convs, embs, _ = self.write_flat_legacy()
        loaded = load_snapshot(self.data_dir)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.token)
        self.assertEqual([c['id'] for c in loaded.conversations],
                         [c['id'] for c in convs])
        np.testing.assert_allclose(loaded.embeddings, embs)

    def test_legacy_token_changes_when_flat_files_change(self):
        self.write_flat_legacy()
        before = load_snapshot(self.data_dir).token
        np.save(os.path.join(self.data_dir, 'embeddings.npy'),
                make_embeddings(3, seed=99))
        after = load_snapshot(self.data_dir).token
        self.assertNotEqual(before, after)

    def test_partial_flat_legacy_is_error_not_empty(self):
        convs = make_conversations(3)
        with open(os.path.join(self.data_dir, 'conversations.json'), 'w') as f:
            json.dump(convs, f)
        # conversations without vectors is a broken index, not "no index"
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestPointerCorruption(StorageTestCase):

    def test_unparseable_pointer_fails_closed_despite_legacy_files(self):
        self.publish_first()
        self.write_flat_legacy()  # valid flat leftovers must not mask this
        with open(os.path.join(self.data_dir, 'current.json'), 'w') as f:
            f.write('{not valid json')
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_dangling_pointer_fails_closed_despite_legacy_files(self):
        published = self.publish_first()
        self.write_flat_legacy()
        pointer_path = os.path.join(self.data_dir, 'current.json')
        with open(pointer_path) as f:
            text = f.read()
        self.assertIn(published.token, text)
        with open(pointer_path, 'w') as f:
            f.write(text.replace(published.token, str(uuid.uuid4())))
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestArtifactCorruption(StorageTestCase):

    def test_tampered_json_artifact_fails_closed(self):
        published = self.publish_first()
        conv_path = os.path.join(published.directory, 'conversations.json')
        with open(conv_path) as f:
            data = json.load(f)
        data[0]['name'] = 'silently edited'
        with open(conv_path, 'w') as f:
            json.dump(data, f)  # still valid JSON; hash check must catch it
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_truncated_vector_artifact_fails_closed(self):
        published = self.publish_first()
        emb_path = os.path.join(published.directory, 'embeddings.npy')
        with open(emb_path, 'r+b') as f:
            f.truncate(16)
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestPublishValidation(StorageTestCase):

    def assert_nothing_published(self):
        self.assertIsNone(load_snapshot(self.data_dir))

    def test_non_finite_embeddings_rejected(self):
        convs, embs, graph = self.make_corpus()
        embs[1, 0] = np.nan
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()

    def test_embedding_row_count_mismatch_rejected(self):
        convs, _, graph = self.make_corpus()
        embs = make_embeddings(len(convs) + 1)
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()

    def test_chunk_map_out_of_range_rejected(self):
        convs, embs, graph = self.make_corpus()
        chunk_embs = make_embeddings(2, seed=3)
        bad_map = [0, len(convs)]  # second chunk points past the corpus
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               chunk_embs, bad_map,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()

    def test_chunks_without_map_rejected(self):
        convs, embs, graph = self.make_corpus()
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               make_embeddings(2, seed=3), None,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()

    def test_graph_edge_to_unknown_conversation_rejected(self):
        convs, embs, graph = self.make_corpus()
        graph['edges'].append({'source': convs[0]['id'],
                               'target': 'conv-nonexistent',
                               'weight': 0.9})
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()

    def test_duplicate_conversation_ids_rejected(self):
        convs, embs, graph = self.make_corpus()
        convs[2]['id'] = convs[0]['id']
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assert_nothing_published()


class TestChunkStateNotInherited(StorageTestCase):

    def test_generation_without_chunks_does_not_inherit_previous(self):
        convs, embs, graph = self.make_corpus()
        first = publish_generation(
            self.data_dir, convs, embs, graph,
            make_embeddings(4, seed=5), [0, 0, 1, 2],
            expected_generation=None, embedding_identity=IDENTITY)

        publish_generation(
            self.data_dir, convs, embs, graph,
            expected_generation=first.token, embedding_identity=IDENTITY)

        loaded = load_snapshot(self.data_dir)
        self.assertIsNone(loaded.chunk_embeddings)
        self.assertIsNone(loaded.chunk_to_conv)


class TestPublicationAtomicity(StorageTestCase):

    def test_failure_at_pointer_switch_leaves_current_intact(self):
        first = self.publish_first()
        convs, embs, graph = self.make_corpus(4, seed=1)

        real_replace = os.replace

        def failing_replace(src, dst, *args, **kwargs):
            if os.path.basename(str(dst)) == 'current.json':
                raise OSError('simulated crash at pointer switch')
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch('os.replace', side_effect=failing_replace):
            with self.assertRaises(OSError):
                publish_generation(
                    self.data_dir, convs, embs, graph,
                    expected_generation=first.token,
                    embedding_identity=IDENTITY)

        loaded = load_snapshot(self.data_dir)
        self.assertEqual(loaded.token, first.token)
        self.assertEqual(loaded.conversations, first.conversations)

    def test_failure_at_first_pointer_switch_stays_no_index(self):
        convs, embs, graph = self.make_corpus()
        real_replace = os.replace

        def failing_replace(src, dst, *args, **kwargs):
            if os.path.basename(str(dst)) == 'current.json':
                raise OSError('simulated crash at pointer switch')
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch('os.replace', side_effect=failing_replace):
            with self.assertRaises(OSError):
                publish_generation(self.data_dir, convs, embs, graph,
                                   expected_generation=None,
                                   embedding_identity=IDENTITY)

        self.assertIsNone(load_snapshot(self.data_dir))


class TestConcurrentWriters(StorageTestCase):

    def test_second_writer_from_same_base_is_rejected(self):
        base = self.publish_first()

        convs_a, embs_a, graph_a = self.make_corpus(4, seed=1)
        winner = publish_generation(
            self.data_dir, convs_a, embs_a, graph_a,
            expected_generation=base.token, embedding_identity=IDENTITY)

        convs_b, embs_b, graph_b = self.make_corpus(5, seed=2)
        with self.assertRaises(StaleGenerationError):
            publish_generation(
                self.data_dir, convs_b, embs_b, graph_b,
                expected_generation=base.token, embedding_identity=IDENTITY)

        # loser must not have clobbered the winner
        self.assertEqual(load_snapshot(self.data_dir).token, winner.token)

    def test_first_publish_expectation_violated_when_index_exists(self):
        self.publish_first()
        convs, embs, graph = self.make_corpus(4, seed=1)
        with self.assertRaises(StaleGenerationError):
            publish_generation(self.data_dir, convs, embs, graph,
                               expected_generation=None,
                               embedding_identity=IDENTITY)


class TestReaderIsolation(StorageTestCase):

    def test_reader_retains_old_snapshot_across_update(self):
        first = self.publish_first()
        held = load_snapshot(self.data_dir)
        held_convs = list(held.conversations)
        held_embs = np.array(held.embeddings, copy=True)

        convs, embs, graph = self.make_corpus(6, seed=4)
        publish_generation(self.data_dir, convs, embs, graph,
                           expected_generation=first.token,
                           embedding_identity=IDENTITY)

        # old generation is retained; the held snapshot stays fully usable
        self.assertEqual(held.token, first.token)
        self.assertEqual(held.conversations, held_convs)
        np.testing.assert_allclose(held.embeddings, held_embs)
        self.assertTrue(os.path.isdir(held.directory))
        with open(os.path.join(held.directory, 'conversations.json')) as f:
            on_disk = json.load(f)
        self.assertEqual([c['id'] for c in on_disk],
                         [c['id'] for c in held_convs])


class TestPointerFsyncDurability(StorageTestCase):

    def test_root_fsync_failure_after_pointer_switch_raises_but_commits(self):
        """os.replace of current.json succeeded; only the durability fsync of
        the data root failed. The error must propagate, but the published
        pointer and generation must remain coherent and loadable."""
        first = self.publish_first()
        convs, embs, graph = self.make_corpus(4, seed=1)

        import core.storage as storage_mod
        real_fsync_dir = storage_mod._fsync_dir
        root = os.path.realpath(self.data_dir)

        def failing_fsync_dir(path):
            if os.path.realpath(path) == root:
                raise OSError('simulated fsync failure on data root')
            return real_fsync_dir(path)

        with mock.patch('core.storage._fsync_dir',
                        side_effect=failing_fsync_dir):
            with self.assertRaises(OSError):
                publish_generation(self.data_dir, convs, embs, graph,
                                   expected_generation=first.token,
                                   embedding_identity=IDENTITY)

        loaded = load_snapshot(self.data_dir)
        self.assertIsNotNone(loaded)
        self.assertNotEqual(loaded.token, first.token)
        self.assertEqual(loaded.conversations, convs)
        self.assertTrue(os.path.isdir(loaded.directory))


class TestLegacyPartialArtifacts(StorageTestCase):

    def test_only_graph_data_is_error_not_none(self):
        graph = make_graph_data(make_conversations(2))
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump(graph, f)
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_only_chunk_artifacts_is_error_not_none(self):
        np.save(os.path.join(self.data_dir, 'chunk_embeddings.npy'),
                make_embeddings(2, seed=3))
        with open(os.path.join(self.data_dir, 'chunk_to_conv.json'), 'w') as f:
            json.dump([0, 1], f)
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestLegacyChunkValidation(StorageTestCase):

    def write_legacy_chunks(self, chunk_embs, chunk_map):
        np.save(os.path.join(self.data_dir, 'chunk_embeddings.npy'),
                chunk_embs)
        with open(os.path.join(self.data_dir, 'chunk_to_conv.json'), 'w') as f:
            json.dump(chunk_map, f)

    def test_legacy_chunk_map_out_of_range_rejected(self):
        self.write_flat_legacy(n=3)
        self.write_legacy_chunks(make_embeddings(2, seed=3), [0, 3])
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_legacy_chunk_embeddings_nan_rejected(self):
        self.write_flat_legacy(n=3)
        chunk_embs = make_embeddings(2, seed=3)
        chunk_embs[0, 0] = np.nan
        self.write_legacy_chunks(chunk_embs, [0, 1])
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_legacy_chunk_map_length_mismatch_rejected(self):
        self.write_flat_legacy(n=3)
        self.write_legacy_chunks(make_embeddings(3, seed=3), [0, 1])
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestPublishWidthConsistency(StorageTestCase):

    def test_chunk_dimension_mismatch_with_conversation_vectors_rejected(self):
        convs, embs, graph = self.make_corpus()  # dim 8
        chunk_embs = make_embeddings(2, dim=4, seed=3)
        with self.assertRaises(PUBLISH_ERRORS):
            publish_generation(self.data_dir, convs, embs, graph,
                               chunk_embs, [0, 1],
                               expected_generation=None,
                               embedding_identity=IDENTITY)
        self.assertIsNone(load_snapshot(self.data_dir))


class TestManifestTampering(StorageTestCase):

    def publish_with_chunks(self):
        convs, embs, graph = self.make_corpus()
        return publish_generation(
            self.data_dir, convs, embs, graph,
            make_embeddings(4, seed=5), [0, 0, 1, 2],
            expected_generation=None, embedding_identity=IDENTITY)

    def rewrite_manifest(self, gen_dir, mutate):
        path = os.path.join(gen_dir, 'manifest.json')
        with open(path) as f:
            manifest = json.load(f)
        mutate(manifest)
        with open(path, 'w') as f:
            json.dump(manifest, f)
        return manifest

    def test_dropping_chunk_vectors_but_keeping_map_rejected(self):
        """Removing chunk_embeddings.npy and its manifest hash while keeping
        chunk_to_conv.json (and its hash) forges a map-only generation; the
        broken pair must fail closed, not silently load without chunks."""
        published = self.publish_with_chunks()
        os.unlink(os.path.join(published.directory, 'chunk_embeddings.npy'))
        self.rewrite_manifest(
            published.directory,
            lambda m: m['artifacts'].pop('chunk_embeddings.npy'))
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_invalid_manifest_schema_rejected_despite_correct_hashes(self):
        published = self.publish_first()

        def mutate(m):
            m['schema'] = 999
        self.rewrite_manifest(published.directory, mutate)
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)

    def test_clusters_artifact_inconsistent_with_graph_rejected(self):
        """clusters.json rewritten (with a matching manifest hash) so it no
        longer agrees with graph_data.clusters must fail closed."""
        published = self.publish_first()
        clusters_path = os.path.join(published.directory, 'clusters.json')
        with open(clusters_path, 'w') as f:
            json.dump({'k': 2, 'clusters': [
                {'id': 0, 'label': 'a'}, {'id': 5, 'label': 'forged'}]}, f)

        import core.storage as storage_mod
        new_hash = storage_mod._sha256(clusters_path)
        self.rewrite_manifest(
            published.directory,
            lambda m: m['artifacts'].__setitem__('clusters.json', new_hash))
        with self.assertRaises(IndexCorruptError):
            load_snapshot(self.data_dir)


class TestCallerMutationDuringStaging(StorageTestCase):

    def test_caller_mutation_at_first_staged_write_does_not_leak(self):
        """publish_generation must own its inputs before staging: a caller
        mutating the conversations list as the first JSON write happens must
        not alter the committed snapshot or disk/return equality."""
        convs, embs, graph = self.make_corpus()
        original = json.loads(json.dumps(convs))

        import core.storage as storage_mod
        real_write_json = storage_mod._write_json
        fired = []

        def mutating_write_json(path, obj):
            if not fired:
                fired.append(True)
                convs[0]['name'] = 'MUTATED BY CALLER'
                convs.append({'id': 'conv-injected', 'messages': []})
            return real_write_json(path, obj)

        with mock.patch('core.storage._write_json',
                        side_effect=mutating_write_json):
            published = publish_generation(
                self.data_dir, convs, embs, graph,
                expected_generation=None, embedding_identity=IDENTITY)

        self.assertTrue(fired)
        loaded = load_snapshot(self.data_dir)
        self.assertEqual(loaded.conversations, original)
        self.assertEqual(published.conversations, loaded.conversations)


def _concurrent_publisher(data_dir, base_token, seed, marker, result_path):
    """Child-process worker: wait for the release marker, then race a
    publish from the shared base generation."""
    import time
    sys.path.insert(0, os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    from core import storage
    convs = make_conversations(3 + seed)
    embs = make_embeddings(3 + seed, seed=seed)
    graph = make_graph_data(convs)
    deadline = time.monotonic() + 10.0
    while not os.path.exists(marker):
        if time.monotonic() > deadline:
            result = 'timeout'
            break
        time.sleep(0.005)
    else:
        try:
            snap = storage.publish_generation(
                data_dir, convs, embs, graph,
                expected_generation=base_token,
                embedding_identity=IDENTITY)
            result = f'committed:{snap.token}'
        except StaleGenerationError:
            result = 'stale'
        except Exception as e:  # pragma: no cover - diagnostic
            result = f'error:{type(e).__name__}:{e}'
    tmp = result_path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(result)
    os.replace(tmp, result_path)


class TestMultiprocessConcurrentPublishers(StorageTestCase):

    def test_two_processes_from_same_base_one_wins_one_stale(self):
        import multiprocessing
        base = self.publish_first()
        marker = os.path.join(self.data_dir, 'release.marker')
        results = [os.path.join(self.data_dir, f'result-{i}.txt')
                   for i in (0, 1)]
        ctx = multiprocessing.get_context('spawn')
        procs = [
            ctx.Process(target=_concurrent_publisher,
                        args=(self.data_dir, base.token, i + 1,
                              marker, results[i]))
            for i in (0, 1)
        ]
        try:
            for p in procs:
                p.start()
            with open(marker, 'w') as f:
                f.write('go')
            for p in procs:
                p.join(timeout=15)
                self.assertFalse(p.is_alive(), 'publisher process hung')
        finally:
            for p in procs:
                if p.is_alive():
                    p.kill()
                    p.join(timeout=5)

        outcomes = []
        for path in results:
            self.assertTrue(os.path.exists(path), 'missing child result')
            with open(path) as f:
                outcomes.append(f.read())
        committed = [o for o in outcomes if o.startswith('committed:')]
        stale = [o for o in outcomes if o == 'stale']
        self.assertEqual(len(committed), 1, f'outcomes: {outcomes}')
        self.assertEqual(len(stale), 1, f'outcomes: {outcomes}')

        winner_token = committed[0].split(':', 1)[1]
        loaded = load_snapshot(self.data_dir)
        self.assertEqual(loaded.token, winner_token)
        self.assertNotEqual(loaded.token, base.token)


if __name__ == '__main__':
    unittest.main()
