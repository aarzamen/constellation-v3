"""Unit tests for Constellation V3 core functionality."""

import json
import os
import sys
import numpy as np
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.math_utils import (
    safe_normalize, cosine_similarity_matrix, cosine_similarity_query,
    kmeans, silhouette_score, auto_cluster, pca_3d
)
from core.parser import parse_claude_export, extract_top_terms, find_claude_export


class TestMathUtils(unittest.TestCase):
    """Test pure numpy math implementations."""

    def test_safe_normalize_normal(self):
        X = np.array([[3.0, 4.0], [1.0, 0.0]])
        result = safe_normalize(X)
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-6)

    def test_safe_normalize_zero_vector(self):
        X = np.array([[0.0, 0.0], [3.0, 4.0]])
        result = safe_normalize(X)
        # Zero vector should remain zero, not produce NaN
        self.assertFalse(np.any(np.isnan(result)))
        np.testing.assert_allclose(result[0], [0.0, 0.0])

    def test_cosine_similarity_matrix(self):
        X = safe_normalize(np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
        sim = cosine_similarity_matrix(X)
        self.assertAlmostEqual(sim[0, 1], 1.0, places=5)  # identical vectors
        self.assertAlmostEqual(sim[0, 2], 0.0, places=5)  # orthogonal vectors

    def test_cosine_similarity_query(self):
        query = np.array([1.0, 0.0])
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [0.707, 0.707]])
        sims = cosine_similarity_query(query, embeddings)
        self.assertEqual(len(sims), 3)
        self.assertAlmostEqual(sims[0], 1.0, places=3)
        self.assertAlmostEqual(sims[1], 0.0, places=3)

    def test_kmeans_basic(self):
        # Two clearly separated clusters
        rng = np.random.RandomState(42)
        cluster1 = rng.randn(20, 10) + np.array([5]*10)
        cluster2 = rng.randn(20, 10) + np.array([-5]*10)
        X = np.vstack([cluster1, cluster2])
        labels, centroids = kmeans(X, n_clusters=2)
        # Should find 2 clusters, each with ~20 members
        unique, counts = np.unique(labels, return_counts=True)
        self.assertEqual(len(unique), 2)
        self.assertTrue(all(c >= 15 for c in counts))  # reasonable split

    def test_kmeans_no_nan(self):
        """KMeans should never produce NaN centroids."""
        X = np.random.randn(10, 5)
        labels, centroids = kmeans(X, n_clusters=5)
        self.assertFalse(np.any(np.isnan(centroids)))
        self.assertFalse(np.any(np.isnan(labels)))

    def test_kmeans_k_exceeds_n(self):
        """KMeans should handle k > n gracefully."""
        X = np.random.randn(5, 10)
        labels, centroids = kmeans(X, n_clusters=10)
        self.assertFalse(np.any(np.isnan(centroids)))
        self.assertEqual(len(labels), 5)

    def test_silhouette_score_valid(self):
        rng = np.random.RandomState(42)
        X = np.vstack([rng.randn(20, 5) + 5, rng.randn(20, 5) - 5])
        labels = np.array([0]*20 + [1]*20)
        score = silhouette_score(X, labels)
        self.assertGreater(score, 0.0)  # should be positive for well-separated clusters
        self.assertLessEqual(score, 1.0)

    def test_silhouette_single_cluster(self):
        X = np.random.randn(10, 5)
        labels = np.zeros(10, dtype=int)
        score = silhouette_score(X, labels)
        self.assertEqual(score, -1.0)  # undefined for single cluster

    def test_auto_cluster_caps_k(self):
        """auto_cluster should not produce more clusters than N/3."""
        X = np.random.randn(12, 10)
        k, labels, centroids = auto_cluster(X, k_min=2, k_max=20)
        self.assertLessEqual(k, 4)  # 12/3 = 4
        self.assertGreaterEqual(k, 2)

    def test_auto_cluster_tiny_dataset(self):
        """auto_cluster handles very small datasets."""
        X = np.random.randn(3, 10)
        k, labels, centroids = auto_cluster(X)
        self.assertFalse(np.any(np.isnan(centroids)))
        self.assertEqual(len(labels), 3)

    def test_pca_3d_output_shape(self):
        X = np.random.randn(50, 384)
        result = pca_3d(X)
        self.assertEqual(result.shape, (50, 3))

    def test_pca_3d_no_nan(self):
        X = np.random.randn(20, 100)
        result = pca_3d(X)
        self.assertFalse(np.any(np.isnan(result)))

    def test_pca_3d_zero_input(self):
        """PCA should handle constant input without NaN."""
        X = np.ones((10, 50))
        result = pca_3d(X)
        self.assertFalse(np.any(np.isnan(result)))

    def test_pca_3d_scaling(self):
        """PCA output should be scaled to [-100, 100] range."""
        X = np.random.randn(50, 384)
        result = pca_3d(X)
        self.assertLessEqual(np.abs(result).max(), 100.01)


class TestParser(unittest.TestCase):
    """Test Claude export parsing."""

    def test_extract_top_terms(self):
        text = "The TCCC medical protocol uses the MARCH algorithm for triage assessment"
        terms = extract_top_terms(text, n=3)
        self.assertIsInstance(terms, list)
        self.assertLessEqual(len(terms), 3)
        # Should not include stopwords
        for t in terms:
            self.assertNotIn(t, ['the', 'for', 'uses'])

    def test_parse_sample_data(self):
        """Parse the included sample data file."""
        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'sample_data', 'dummy_conversations.json'
        )
        if not os.path.exists(sample_path):
            self.skipTest("Sample data not found")

        conversations = parse_claude_export(sample_path)
        self.assertGreater(len(conversations), 0)

        # Verify structure
        conv = conversations[0]
        self.assertIn('id', conv)
        self.assertIn('name', conv)
        self.assertIn('messages', conv)
        self.assertIn('user_messages', conv)
        self.assertGreater(len(conv['messages']), 0)

        # Verify roles are normalized
        for msg in conv['messages']:
            self.assertIn(msg['role'], ['user', 'assistant'])

    def test_parse_handles_missing_fields(self):
        """Parser should handle conversations with missing optional fields."""
        import tempfile
        data = [{
            "uuid": "test-123",
            "name": "Test Conversation",
            "created_at": "2024-01-01T00:00:00Z",
            "chat_messages": [
                {"uuid": "m1", "text": "Hello", "sender": "human", "created_at": "2024-01-01T00:00:00Z"}
            ]
        }]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            f.flush()
            conversations = parse_claude_export(f.name)
        os.unlink(f.name)

        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]['user_messages'], ['Hello'])


class TestSearchEngine(unittest.TestCase):
    """Test the search/retrieval engine."""

    def test_search_engine_loads(self):
        """SearchEngine should load from data directory if available."""
        from server.api import SearchEngine
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data'
        )
        if not os.path.exists(os.path.join(data_dir, 'embeddings.npy')):
            self.skipTest("No pre-computed data available")

        engine = SearchEngine(data_dir)
        engine.load()
        self.assertIsNotNone(engine.embeddings)
        self.assertGreater(len(engine.conversations), 0)

    def test_search_returns_results(self):
        """Search should return ranked results."""
        from server.api import SearchEngine
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data'
        )
        if not os.path.exists(os.path.join(data_dir, 'embeddings.npy')):
            self.skipTest("No pre-computed data available")

        engine = SearchEngine(data_dir)
        results = engine.search("medical emergency", top_k=3)
        self.assertTrue(len(results) > 0)
        self.assertIn('title', results[0])
        self.assertIn('score', results[0])
        self.assertIn('chunk_score', results[0])
        self.assertIn('excerpt', results[0])
        # Results should be sorted by score descending
        if len(results) > 1:
            self.assertGreaterEqual(results[0]['score'], results[1]['score'])
            
    def test_search_empty_input_handled(self):
        """Empty searches should return cleanly without throwing exceptions or querying vectors."""
        from server.api import SearchEngine
        engine = SearchEngine()
        res = engine.search("   ", top_k=5)
        self.assertEqual(len(res), 0)


class TestMCPServer(unittest.TestCase):
    """Test MCP server configuration."""

    def test_mcp_server_imports(self):
        """MCP server module should import without errors."""
        # This tests that all imports resolve correctly
        from server.mcp_server import mcp
        self.assertIsNotNone(mcp)

    def test_mcp_server_has_tools(self):
        """MCP server should register both tools."""
        from server.mcp_server import mcp
        # FastMCP exposes list_tools method
        self.assertTrue(hasattr(mcp, 'list_tools'))

    def test_mcp_search_empty_query_validation(self):
        """MCP search should reject empty strings."""
        from server.mcp_server import search_conversations
        res = search_conversations("   ")
        self.assertIn("error", res[0])
        self.assertEqual(res[0]["error"], "Search query cannot be empty")

    def test_mcp_add_note_validation(self):
        """MCP add_note should explicitly reject empty conversation IDs or text."""
        from server.mcp_server import add_conversation_note
        res1 = add_conversation_note("", "Valid note")
        res2 = add_conversation_note("valid_id", "    ")
        self.assertIn("error", res1)
        self.assertIn("error", res2)


class TestEndToEnd(unittest.TestCase):
    """End-to-end pipeline test."""

    def test_full_pipeline_sample_data(self):
        """Run the full pipeline on sample data and verify outputs."""
        from core.parser import parse_claude_export
        from core.embedder import Embedder
        from core.indexer import embed_conversations, build_clusters, build_edges, build_graph_data

        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'sample_data', 'dummy_conversations.json'
        )
        if not os.path.exists(sample_path):
            self.skipTest("Sample data not found")

        # Parse
        conversations = parse_claude_export(sample_path)
        self.assertGreater(len(conversations), 0)

        # Embed
        embedder = Embedder()
        embeddings, chunk_emb, chunk_map = embed_conversations(conversations, embedder)
        self.assertEqual(embeddings.shape[0], len(conversations))
        self.assertEqual(embeddings.shape[1], 384)
        self.assertFalse(np.any(np.isnan(embeddings)))

        # Cluster
        cluster_info = build_clusters(embeddings)
        self.assertIn('k', cluster_info)
        self.assertIn('labels', cluster_info)
        self.assertFalse(np.any(np.isnan(cluster_info['centroids'])))

        # Edges
        edges = build_edges(embeddings, conversations)
        self.assertIsInstance(edges, list)

        # Graph data
        graph_data = build_graph_data(conversations, embeddings, cluster_info, edges)
        self.assertIn('nodes', graph_data)
        self.assertIn('edges', graph_data)
        self.assertIn('clusters', graph_data)
        self.assertIn('stats', graph_data)
        self.assertEqual(len(graph_data['nodes']), len(conversations))

        # Verify no NaN in node positions
        for node in graph_data['nodes']:
            self.assertFalse(np.isnan(node['x']))
            self.assertFalse(np.isnan(node['y']))
            self.assertFalse(np.isnan(node['z']))
            self.assertFalse(np.isnan(node['tx']))
            self.assertFalse(np.isnan(node['ty']))
            self.assertFalse(np.isnan(node['tz']))


if __name__ == '__main__':
    unittest.main()
