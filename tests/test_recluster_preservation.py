"""Recluster/publication preservation tests (red first).

Pins two independent review findings:

1. save_pipeline_output publishing the FIRST generation directly over a
   legacy flat archive must migrate embedded notes to the sidecar before
   it strips the ``notes`` field and the new current.json pointer blocks
   legacy migration forever.
2. A successful HTTP recluster commit must return the committed graph;
   the handler's extra post-commit search_engine.load() must not turn a
   successful publish into a 500 when a later reload fails.

Synthetic data in temp dirs only; no sockets, no real embedding model.
"""

import json
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notes import load_notes
from core.storage import load_snapshot
from tests.test_generation_readers import (
    DIM,
    RestTestCase,
    fake_http_get,
    identity_for,
    make_conversations,
    make_embeddings,
    make_graph_data,
)


class TestLegacyNotesSurvivePublication(unittest.TestCase):

    def setUp(self):
        import tempfile
        import shutil
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def seed_legacy_flat(self, convs):
        with open(os.path.join(self.data_dir, 'conversations.json'), 'w') as f:
            json.dump(convs, f)
        with open(os.path.join(self.data_dir, 'embeddings.npy'), 'wb') as f:
            np.save(f, make_embeddings(len(convs)))
        with open(os.path.join(self.data_dir, 'graph_data.json'), 'w') as f:
            json.dump(make_graph_data(convs), f)

    def test_first_publication_migrates_embedded_notes_first(self):
        from core.indexer import save_pipeline_output

        convs = make_conversations(3)
        convs[0]['notes'] = [{'text': 'legacy keepsake',
                              'created_at': '2024-01-01T00:00:00Z'}]
        self.seed_legacy_flat(convs)
        self.assertFalse(
            os.path.exists(os.path.join(self.data_dir, 'notes.json')))

        legacy = load_snapshot(self.data_dir)
        self.assertTrue(legacy.token.startswith('legacy-'))

        for conv in legacy.conversations:
            conv['user_messages'] = [m['text'] for m in conv['messages']]
        committed = save_pipeline_output(
            legacy.conversations, legacy.embeddings, None, None,
            make_graph_data(legacy.conversations), self.data_dir,
            expected_generation=legacy.token,
            embedding_identity=identity_for('model-a'))

        # The published corpus no longer embeds notes...
        for conv in committed.conversations:
            self.assertNotIn('notes', conv)
        # ...so they must have been migrated to the sidecar BEFORE the
        # pointer made the flat archive permanently ineligible.
        loaded = load_notes(self.data_dir)
        texts = [n.get('text') for notes in loaded.values() for n in notes]
        self.assertIn('legacy keepsake', texts,
                      'embedded legacy notes lost at first publication')


class TestReclusterCommitSurvivesReloadFailure(RestTestCase):

    def stub_indexer(self, build_graph):
        import core.indexer as indexer
        for name, fn in (('build_clusters', lambda *a, **k: {'stub': True}),
                         ('build_edges', lambda *a, **k: []),
                         ('build_graph_data', build_graph)):
            p = mock.patch.object(indexer, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def test_committed_recluster_returns_200_despite_reload_failure(self):
        convs = make_conversations(3)
        gen1 = self.publish(convs)
        new_graph = make_graph_data(convs)
        new_graph['stats']['reclustered'] = True
        # save_pipeline_output rewrites these stats from the actual vectors
        # and recorded identity; the committed/served graph carries them.
        new_graph['stats']['embeddingDim'] = DIM
        new_graph['stats']['embeddingModel'] = 'model-a'
        self.stub_indexer(lambda *a, **k: new_graph)

        engine = self.http_server.search_engine
        # The handler's own snapshot capture must see gen1; only the eager
        # post-commit reload fails.
        real_state = engine.load()
        reload_p = mock.patch.object(
            engine, 'load',
            side_effect=[real_state,
                         RuntimeError('simulated post-commit reload failure')])
        reload_p.start()
        self.addCleanup(reload_p.stop)

        status, body = fake_http_get('/api/recluster?k=2')

        committed = load_snapshot(self.data_dir)
        self.assertNotEqual(committed.token, gen1.token,
                            'recluster did not commit a new generation')
        self.assertEqual(committed.graph_data, new_graph)
        # The commit succeeded, so the client must get the committed graph,
        # not a 500 from an eager post-commit engine reload.
        self.assertEqual(status, 200,
                         f'committed recluster reported failure: {body}')
        self.assertEqual(body, new_graph)


if __name__ == '__main__':
    unittest.main()
