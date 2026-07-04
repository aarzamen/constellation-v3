"""Tests for the Google-source parsers (D3 NotebookLM, D4 Gemini activity,
D5 AI Studio) + save_pipeline_output provenance-field preservation."""

import datetime
import json
import os
import tempfile
import unittest

import numpy as np

from core.notebooklm_parser import parse_session_html
from core.gemini_activity_parser import extract_entries, sessionize
from core.aistudio_parser import parse_prompt_file
from core.indexer import save_pipeline_output


class TestNotebookLM(unittest.TestCase):
    def test_roles_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            nb = os.path.join(td, 'My Notebook', 'Chat History')
            os.makedirs(nb)
            p = os.path.join(nb, 'Chat Session - abcd1234-0000.html')
            open(p, 'w').write(
                'MODEL: <p>Hello, how can I help?</p>\n'
                'USER: <p>Explain X.</p>\n'
                'MODEL: <p>X is a thing.</p>\n')
            conv = parse_session_html(p)
            self.assertEqual(conv['provider'], 'notebooklm')
            self.assertEqual(conv['id'], 'notebooklm_abcd1234-0000')
            self.assertEqual(conv['name'], 'My Notebook')
            roles = [m['role'] for m in conv['messages']]
            self.assertEqual(roles, ['assistant', 'user', 'assistant'])
            self.assertEqual(conv['user_messages'], ['Explain X.'])


class TestGeminiActivity(unittest.TestCase):
    def _html(self):
        def cell(prompt, ts, resp):
            return (f'<div class="outer-cell"><div class="content-cell '
                    f'mdl-typography--body-1">Prompted\xa0{prompt}<br>{ts}<br>'
                    f'<p>{resp}</p></div><div class="caption">Products: '
                    f'Gemini Apps</div></div>')
        # two prompts 20 min apart (one session), a third 3h later (new session)
        return (cell('first question', 'Jul 2, 2026, 5:00:00 PM PDT', 'ans1')
                + cell('second question', 'Jul 2, 2026, 5:20:00 PM PDT', 'ans2')
                + cell('later question', 'Jul 2, 2026, 8:30:00 PM PDT', 'ans3'))

    def test_extract_and_sessionize(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'MyActivity.html')
            open(p, 'w', encoding='utf-8').write(self._html())
            entries = extract_entries(p)
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0]['prompt'], 'first question')
            self.assertEqual(entries[0]['response'], 'ans1')       # response captured
            self.assertNotIn('Products', entries[0]['response'])    # caption stripped
            convs = sessionize(entries, gap_minutes=30)
            self.assertEqual(len(convs), 2)                         # 30-min gap split
            self.assertTrue(all(c['inferred_grouping'] for c in convs))
            self.assertTrue(all(c['provider'] == 'gemini' for c in convs))
            # first session: 2 prompts -> user+assistant interleaved
            self.assertEqual(convs[0]['name'], 'first question')
            self.assertEqual(convs[0]['user_messages'], ['first question', 'second question'])


class TestAIStudio(unittest.TestCase):
    def _write(self, path, chunks, model='models/gemini-2.5-pro'):
        json.dump({'runSettings': {'model': model},
                   'systemInstruction': {'text': 'be terse'},
                   'chunkedPrompt': {'chunks': chunks}}, open(path, 'w'))

    def test_chat_type_carries_model(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'My Prompt')
            self._write(p, [{'role': 'user', 'text': 'hi'},
                            {'role': 'model', 'text': 'hello'},
                            {'role': 'model', 'text': 'thinking', 'isThought': True}])
            conv = parse_prompt_file(p)
            self.assertEqual(conv['provider'], 'aistudio')
            self.assertEqual(conv['model'], 'models/gemini-2.5-pro')
            self.assertEqual(conv['system_instruction'], 'be terse')
            self.assertEqual([m['role'] for m in conv['messages']], ['user', 'assistant'])
            self.assertNotIn('thinking', ' '.join(m['text'] for m in conv['messages']))

    def test_non_chat_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'Draft')
            self._write(p, [{'role': 'user', 'text': 'just a draft prompt'}])
            self.assertIsNone(parse_prompt_file(p))


class TestSavePreservesProvenance(unittest.TestCase):
    def test_model_and_flag_survive_save(self):
        with tempfile.TemporaryDirectory() as td:
            convs = [{'id': 'a', 'name': 'n', 'created_at': '', 'provider': 'aistudio',
                      'model': 'models/gemini-2.5-pro', 'inferred_grouping': True,
                      'system_instruction': 'sys', 'messages': []}]
            graph = {'stats': {'clusterCount': 1}, 'clusters': []}
            save_pipeline_output(convs, np.zeros((1, 384)), None, None, graph, td)
            saved = json.load(open(os.path.join(td, 'conversations.json')))[0]
            self.assertEqual(saved['model'], 'models/gemini-2.5-pro')
            self.assertTrue(saved['inferred_grouping'])
            self.assertEqual(saved['system_instruction'], 'sys')
            self.assertNotIn('user_messages', saved)


if __name__ == '__main__':
    unittest.main()
