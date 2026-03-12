"""Tests for Gemini AI Studio export parser."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gemini_parser import (
    parse_gemini_export, extract_gemini_messages, is_gemini_chat_file,
    generate_gemini_id,
)


def make_gemini_chat(chunks=None, model='gemini-2.5-pro'):
    """Build a minimal Gemini AI Studio chat file."""
    if chunks is None:
        chunks = [
            {'role': 'user', 'parts': [{'text': 'Hello'}], 'tokenCount': 1},
            {'role': 'model', 'parts': [{'text': 'Hi there!'}], 'tokenCount': 3},
        ]
    return {
        'runSettings': {'model': model, 'temperature': 0.9},
        'chunkedPrompt': {'chunks': chunks},
    }


class TestGeminiParser(unittest.TestCase):

    def test_basic_parse(self):
        """Parse a minimal Gemini chat with user/model messages."""
        data = make_gemini_chat()
        messages = extract_gemini_messages(data)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertEqual(messages[0]['text'], 'Hello')
        self.assertEqual(messages[1]['role'], 'assistant')
        self.assertEqual(messages[1]['text'], 'Hi there!')

    def test_skips_thinking_blocks(self):
        """Messages with isThought=True are excluded."""
        chunks = [
            {'role': 'user', 'parts': [{'text': 'Solve this'}], 'tokenCount': 2},
            {'role': 'model', 'parts': [{'text': 'Let me think...'}],
             'isThought': True, 'tokenCount': 5},
            {'role': 'model', 'parts': [{'text': 'The answer is 42'}], 'tokenCount': 4},
        ]
        data = make_gemini_chat(chunks)
        messages = extract_gemini_messages(data)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]['text'], 'The answer is 42')

    def test_skips_non_chat_files(self):
        """Files with known extensions are not detected as chat files."""
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(b'not a chat')
            f.flush()
            self.assertFalse(is_gemini_chat_file(f.name))
        os.unlink(f.name)

        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'\x89PNG')
            f.flush()
            self.assertFalse(is_gemini_chat_file(f.name))
        os.unlink(f.name)

    def test_detects_gemini_chat_file(self):
        """Extensionless file with chunkedPrompt marker is detected."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='', delete=False) as f:
            json.dump(make_gemini_chat(), f)
            f.flush()
            self.assertTrue(is_gemini_chat_file(f.name))
        os.unlink(f.name)

    def test_generates_stable_ids(self):
        """Same filename always produces same conversation ID."""
        id1 = generate_gemini_id('/path/to/My Chat')
        id2 = generate_gemini_id('/different/path/My Chat')
        self.assertEqual(id1, id2)  # same filename basename

        id3 = generate_gemini_id('/path/to/Other Chat')
        self.assertNotEqual(id1, id3)

    def test_id_format(self):
        """Generated ID has UUID-like format."""
        gid = generate_gemini_id('/path/to/test')
        parts = gid.split('-')
        self.assertEqual(len(parts), 5)

    def test_handles_missing_timestamps(self):
        """Conversations without timestamps get non-empty string from mtime."""
        tmpdir = tempfile.mkdtemp()
        filepath = os.path.join(tmpdir, 'Test Chat')
        with open(filepath, 'w') as f:
            json.dump(make_gemini_chat(), f)

        result = parse_gemini_export(tmpdir)
        self.assertEqual(len(result), 1)
        # created_at should be populated from file mtime, not crash
        self.assertIsInstance(result[0]['created_at'], str)

        os.unlink(filepath)
        os.rmdir(tmpdir)

    def test_provider_field(self):
        """All parsed conversations have provider='gemini'."""
        tmpdir = tempfile.mkdtemp()
        filepath = os.path.join(tmpdir, 'Chat 1')
        with open(filepath, 'w') as f:
            json.dump(make_gemini_chat(), f)

        result = parse_gemini_export(tmpdir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['provider'], 'gemini')

        os.unlink(filepath)
        os.rmdir(tmpdir)

    def test_skips_empty_chunks(self):
        """Chat with no user/model messages is skipped."""
        data = make_gemini_chat(chunks=[])
        messages = extract_gemini_messages(data)
        self.assertEqual(len(messages), 0)

    def test_multiple_files(self):
        """Parser scans directory and finds multiple chat files."""
        tmpdir = tempfile.mkdtemp()

        for i in range(3):
            filepath = os.path.join(tmpdir, f'Chat {i}')
            with open(filepath, 'w') as f:
                json.dump(make_gemini_chat(), f)

        # Also add a non-chat file
        with open(os.path.join(tmpdir, 'image.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n')

        result = parse_gemini_export(tmpdir)
        self.assertEqual(len(result), 3)

        # Cleanup
        for entry in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, entry))
        os.rmdir(tmpdir)

    def test_output_format(self):
        """Output has same keys as other parsers."""
        tmpdir = tempfile.mkdtemp()
        filepath = os.path.join(tmpdir, 'Test')
        with open(filepath, 'w') as f:
            json.dump(make_gemini_chat(), f)

        result = parse_gemini_export(tmpdir)
        expected_keys = {'id', 'name', 'created_at', 'provider', 'messages', 'user_messages'}
        self.assertEqual(set(result[0].keys()), expected_keys)

        os.unlink(filepath)
        os.rmdir(tmpdir)


if __name__ == '__main__':
    unittest.main()
