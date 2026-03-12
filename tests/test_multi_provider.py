"""Integration tests for multi-provider support."""

import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parser import parse_claude_export
from core.chatgpt_parser import parse_chatgpt_export


def make_claude_data():
    """Create minimal Claude export data."""
    return [{
        'uuid': 'claude-conv-001',
        'name': 'Claude Test Chat',
        'created_at': '2025-01-15T10:00:00Z',
        'chat_messages': [
            {'sender': 'human', 'text': 'Hello Claude', 'created_at': '2025-01-15T10:00:00Z'},
            {'sender': 'assistant', 'text': 'Hello! How can I help?', 'created_at': '2025-01-15T10:00:01Z'},
        ]
    }]


def make_chatgpt_data():
    """Create minimal ChatGPT export data."""
    return [{
        'id': 'chatgpt-conv-001',
        'title': 'ChatGPT Test Chat',
        'create_time': 1705312800.0,
        'mapping': {
            'root': {'id': 'root', 'message': None, 'parent': None, 'children': ['n1']},
            'n1': {
                'id': 'n1', 'parent': 'root', 'children': ['n2'],
                'message': {
                    'id': 'm1', 'author': {'role': 'user'},
                    'content': {'content_type': 'text', 'parts': ['Hello ChatGPT']},
                    'create_time': 1705312800.0, 'weight': 1.0,
                },
            },
            'n2': {
                'id': 'n2', 'parent': 'n1', 'children': [],
                'message': {
                    'id': 'm2', 'author': {'role': 'assistant'},
                    'content': {'content_type': 'text', 'parts': ['Hi there!']},
                    'create_time': 1705312810.0, 'weight': 1.0,
                },
            },
        },
        'current_node': 'n2',
    }]


class TestMultiProvider(unittest.TestCase):

    def test_claude_parser_sets_provider(self):
        """parse_claude_export sets provider='claude' on all conversations."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(make_claude_data(), f)
            f.flush()
            convs = parse_claude_export(f.name)
        os.unlink(f.name)

        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]['provider'], 'claude')

    def test_chatgpt_parser_sets_provider(self):
        """parse_chatgpt_export sets provider='chatgpt' on all conversations."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(make_chatgpt_data(), f)
            f.flush()
            convs = parse_chatgpt_export(f.name)
        os.unlink(f.name)

        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]['provider'], 'chatgpt')

    def test_mixed_provider_format_consistency(self):
        """Both parsers produce conversations with identical key sets."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f1:
            json.dump(make_claude_data(), f1)
            f1.flush()
            claude_convs = parse_claude_export(f1.name)
        os.unlink(f1.name)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f2:
            json.dump(make_chatgpt_data(), f2)
            f2.flush()
            gpt_convs = parse_chatgpt_export(f2.name)
        os.unlink(f2.name)

        claude_keys = set(claude_convs[0].keys())
        gpt_keys = set(gpt_convs[0].keys())
        self.assertEqual(claude_keys, gpt_keys)

    def test_provider_in_graph_data(self):
        """graph_data nodes have 'provider' field, stats have 'providers' dict."""
        from core.indexer import build_graph_data, build_clusters, build_edges

        # Create mixed conversations
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f1:
            json.dump(make_claude_data(), f1)
            f1.flush()
            claude_convs = parse_claude_export(f1.name)
        os.unlink(f1.name)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f2:
            json.dump(make_chatgpt_data(), f2)
            f2.flush()
            gpt_convs = parse_chatgpt_export(f2.name)
        os.unlink(f2.name)

        all_convs = claude_convs + gpt_convs

        # Create fake embeddings (skip actual model)
        embeddings = np.random.randn(len(all_convs), 384).astype(np.float32)
        cluster_info = build_clusters(embeddings)
        edges = build_edges(embeddings, all_convs)
        graph_data = build_graph_data(all_convs, embeddings, cluster_info, edges)

        # Check provider in nodes
        providers_found = set()
        for node in graph_data['nodes']:
            self.assertIn('provider', node)
            providers_found.add(node['provider'])

        self.assertIn('claude', providers_found)
        self.assertIn('chatgpt', providers_found)

        # Check providers in stats
        self.assertIn('providers', graph_data['stats'])
        self.assertEqual(graph_data['stats']['providers']['claude'], 1)
        self.assertEqual(graph_data['stats']['providers']['chatgpt'], 1)


if __name__ == '__main__':
    unittest.main()
