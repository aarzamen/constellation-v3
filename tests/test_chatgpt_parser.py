"""Tests for ChatGPT (OpenAI) export parser."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.chatgpt_parser import (
    parse_chatgpt_export, linearize_conversation, should_include_message,
    extract_text_from_parts, epoch_to_iso, normalize_title,
)


def make_node(node_id, parent=None, children=None, role='user',
              text='Hello', content_type='text', weight=1.0, create_time=None):
    """Helper to build a ChatGPT mapping node."""
    msg = {
        'id': f'msg-{node_id}',
        'author': {'role': role},
        'content': {'content_type': content_type, 'parts': [text] if text else []},
        'create_time': create_time,
        'weight': weight,
    }
    return {
        'id': node_id,
        'message': msg,
        'parent': parent,
        'children': children or [],
    }


def make_root_node(node_id='root', children=None):
    """Helper to build a root node (no message)."""
    return {
        'id': node_id,
        'message': None,
        'parent': None,
        'children': children or [],
    }


def make_conversation(nodes_list, current_node, conv_id='conv-1',
                      title='Test Chat', create_time=1682368832.626):
    """Build a full ChatGPT conversation dict from a list of nodes."""
    mapping = {n['id']: n for n in nodes_list}
    return {
        'id': conv_id,
        'title': title,
        'create_time': create_time,
        'update_time': create_time,
        'mapping': mapping,
        'current_node': current_node,
    }


class TestChatGPTParser(unittest.TestCase):

    def test_basic_linear_conversation(self):
        """Parse a simple 3-message conversation (root -> user -> assistant)."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=['n2'],
                        role='user', text='Hello there', create_time=1682369000.0)
        n2 = make_node('n2', parent='n1', children=[],
                        role='assistant', text='Hi! How can I help?', create_time=1682369010.0)
        conv = make_conversation([root, n1, n2], current_node='n2')

        messages = linearize_conversation(conv)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertEqual(messages[0]['text'], 'Hello there')
        self.assertEqual(messages[1]['role'], 'assistant')
        self.assertEqual(messages[1]['text'], 'Hi! How can I help?')

    def test_branching_conversation_follows_current_node(self):
        """When a user edits a message creating a fork, only the active branch is extracted."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=['n2', 'n3'],
                        role='user', text='Original question')
        n2 = make_node('n2', parent='n1', children=[],
                        role='assistant', text='Answer to original')
        n3 = make_node('n3', parent='n1', children=['n4'],
                        role='user', text='Edited question')
        n4 = make_node('n4', parent='n3', children=[],
                        role='assistant', text='Answer to edited')
        conv = make_conversation([root, n1, n2, n3, n4], current_node='n4')

        messages = linearize_conversation(conv)
        # Should follow: n1 (original question via parent chain) -> n3 (edited) -> n4
        # Wait, the backward walk from n4: n4 -> n3 -> n1 -> root
        # n3 is role=user with text='Edited question'
        # n1 is role=user with text='Original question'
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]['text'], 'Original question')
        self.assertEqual(messages[1]['text'], 'Edited question')
        self.assertEqual(messages[2]['text'], 'Answer to edited')

    def test_skips_system_messages(self):
        """System role messages are excluded from output."""
        root = make_root_node('root', children=['sys'])
        sys_node = make_node('sys', parent='root', children=['n1'],
                              role='system', text='You are a helpful assistant')
        n1 = make_node('n1', parent='sys', children=[],
                        role='user', text='Hi')
        conv = make_conversation([root, sys_node, n1], current_node='n1')

        messages = linearize_conversation(conv)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['role'], 'user')

    def test_skips_tool_messages(self):
        """Tool role messages are excluded."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=['tool1'],
                        role='user', text='Search for X')
        tool1 = make_node('tool1', parent='n1', children=['n2'],
                           role='tool', text='Tool result')
        n2 = make_node('n2', parent='tool1', children=[],
                        role='assistant', text='Here are the results')
        conv = make_conversation([root, n1, tool1, n2], current_node='n2')

        messages = linearize_conversation(conv)
        roles = [m['role'] for m in messages]
        self.assertNotIn('tool', roles)
        self.assertEqual(len(messages), 2)

    def test_skips_non_text_content(self):
        """Messages with content_type != 'text' are excluded."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=['n2'],
                        role='user', text='Draw a cat')
        n2 = make_node('n2', parent='n1', children=[],
                        role='assistant', text=None, content_type='image')
        # Fix: n2 needs no text parts
        n2['message']['content']['parts'] = []
        conv = make_conversation([root, n1, n2], current_node='n2')

        messages = linearize_conversation(conv)
        self.assertEqual(len(messages), 1)  # only user message

    def test_skips_image_parts(self):
        """Parts that are dicts (image references) are skipped, string parts kept."""
        parts = ['Some text', {'type': 'image', 'url': 'http://example.com'}, 'more text']
        result = extract_text_from_parts(parts)
        self.assertEqual(result, 'Some text more text')

    def test_skips_zero_weight_messages(self):
        """Deleted messages (weight=0) are excluded."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=['n2'],
                        role='user', text='Delete me', weight=0)
        n2 = make_node('n2', parent='n1', children=[],
                        role='assistant', text='Response')
        conv = make_conversation([root, n1, n2], current_node='n2')

        messages = linearize_conversation(conv)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['text'], 'Response')

    def test_skips_empty_conversations(self):
        """Conversations with no current_node or empty mapping are skipped."""
        # No current_node
        conv1 = {'id': 'c1', 'title': 'Empty', 'mapping': {'root': make_root_node()}}
        self.assertEqual(linearize_conversation(conv1), [])

        # Empty mapping
        conv2 = {'id': 'c2', 'title': 'Empty', 'mapping': {}, 'current_node': 'x'}
        self.assertEqual(linearize_conversation(conv2), [])

    def test_timestamp_conversion(self):
        """Unix epoch floats are converted to ISO 8601 strings."""
        result = epoch_to_iso(1682368832.626)
        self.assertIn('2023-04-24', result)
        self.assertIn('T', result)

        # None/zero
        self.assertEqual(epoch_to_iso(None), '')
        self.assertEqual(epoch_to_iso(0), '')

    def test_title_normalization(self):
        """'New chat', None, and empty string titles become 'Untitled'."""
        self.assertEqual(normalize_title('New chat'), 'Untitled')
        self.assertEqual(normalize_title(None), 'Untitled')
        self.assertEqual(normalize_title(''), 'Untitled')
        self.assertEqual(normalize_title('   '), 'Untitled')
        self.assertEqual(normalize_title('My Chat'), 'My Chat')

    def test_output_format_matches_claude_parser(self):
        """Output dict has same keys as parse_claude_export output."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=[],
                        role='user', text='Hello')
        conv = make_conversation([root, n1], current_node='n1')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([conv], f)
            f.flush()
            result = parse_chatgpt_export(f.name)
        os.unlink(f.name)

        self.assertEqual(len(result), 1)
        expected_keys = {'id', 'name', 'created_at', 'provider', 'messages', 'user_messages'}
        self.assertEqual(set(result[0].keys()), expected_keys)

    def test_provider_field_is_chatgpt(self):
        """All parsed conversations have provider='chatgpt'."""
        root = make_root_node('root', children=['n1'])
        n1 = make_node('n1', parent='root', children=[],
                        role='user', text='Hello')
        conv = make_conversation([root, n1], current_node='n1')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([conv], f)
            f.flush()
            result = parse_chatgpt_export(f.name)
        os.unlink(f.name)

        for c in result:
            self.assertEqual(c['provider'], 'chatgpt')

    def test_circular_reference_protection(self):
        """Parser doesn't infinite loop on circular parent references."""
        # Create a cycle: n1 -> n2 -> n1
        n1 = make_node('n1', parent='n2', children=['n2'],
                        role='user', text='Loop 1')
        n2 = make_node('n2', parent='n1', children=['n1'],
                        role='assistant', text='Loop 2')
        conv = {
            'id': 'circular',
            'title': 'Circular',
            'create_time': 1682368832.0,
            'mapping': {'n1': n1, 'n2': n2},
            'current_node': 'n2',
        }

        # Should not hang — visited set breaks the loop
        messages = linearize_conversation(conv)
        self.assertLessEqual(len(messages), 2)

    def test_large_conversation(self):
        """Conversation with 200+ messages parses correctly."""
        nodes = [make_root_node('root', children=['n0'])]
        for i in range(200):
            role = 'user' if i % 2 == 0 else 'assistant'
            parent = 'root' if i == 0 else f'n{i-1}'
            children = [f'n{i+1}'] if i < 199 else []
            nodes.append(make_node(f'n{i}', parent=parent, children=children,
                                    role=role, text=f'Message {i}'))
        conv = make_conversation(nodes, current_node='n199')

        messages = linearize_conversation(conv)
        self.assertEqual(len(messages), 200)

    def test_parse_full_file(self):
        """Full parse with multiple conversations."""
        root1 = make_root_node('r1', children=['a1'])
        a1 = make_node('a1', parent='r1', children=[], role='user', text='First chat')
        conv1 = make_conversation([root1, a1], current_node='a1',
                                   conv_id='conv-1', title='Chat 1')

        root2 = make_root_node('r2', children=['b1'])
        b1 = make_node('b1', parent='r2', children=['b2'], role='user', text='Second chat')
        b2 = make_node('b2', parent='b1', children=[], role='assistant', text='Reply')
        conv2 = make_conversation([root2, b1, b2], current_node='b2',
                                   conv_id='conv-2', title='Chat 2')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([conv1, conv2], f)
            f.flush()
            result = parse_chatgpt_export(f.name)
        os.unlink(f.name)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], 'Chat 1')
        self.assertEqual(result[1]['name'], 'Chat 2')
        self.assertEqual(len(result[1]['messages']), 2)


if __name__ == '__main__':
    unittest.main()
