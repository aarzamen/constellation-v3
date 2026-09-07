"""Concurrency and durability tests for core.notes and launch.run_pipeline_embed.

Coordination happens at the real persistence boundary (os.replace) or in
separate processes, so a correctly locked implementation cannot bypass the
gate by using internal helpers instead of a patched public function. All
waits are bounded and workers are reaped before temp directories are
removed. All fixtures are synthetic temp directories.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
# Keep any import-time data-dir resolution away from real user data.
os.environ.setdefault('CONSTELLATION_DATA_DIR',
                      tempfile.mkdtemp(prefix='constellation-test-env-'))

import numpy as np

from core import indexer, notes
import launch

JOIN_TIMEOUT = 10

CORRUPT_BYTES = b'{"conv": [{"text": "irreplaceable user words"'


def _conv(cid, text, provider='claude'):
    return {'id': cid, 'name': text, 'provider': provider,
            'created_at': '2026-01-01T00:00:00Z',
            'messages': [{'role': 'user', 'text': text,
                          'timestamp': '2026-01-01T00:00:00Z'}],
            'user_messages': [text]}


def _graph(cs, label='current'):
    return {'nodes': [{'id': c['id'], 'cluster': 0, 'clusterLabel': label}
                      for c in cs],
            'clusters': [{'id': 0, 'label': label}],
            'stats': {'clusterCount': 1, 'edgeCount': 0}}


class _FakeEmbedder:
    dim = 2

    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts, **kwargs):
        return np.array([[0.0, 1.0] for _ in texts])

    def embed_query(self, query):
        return np.array([[0.0, 1.0]])


class _ReplaceGate:
    """Pause the Nth call to os.replace until released.

    Gating at the actual persistence boundary forces the second writer to
    overlap the first writer's read-modify-replace, and a correctly locked
    implementation cannot dodge the gate by calling internal helpers.
    Every wait is bounded, so a serialized implementation cannot hang here.
    """

    def __init__(self, pause_call=1):
        self._real_replace = os.replace  # bound before patching
        self._counter_lock = threading.Lock()
        self._count = 0
        self._pause_call = pause_call
        self.paused = threading.Event()
        self.release = threading.Event()

    def __call__(self, src, dst, *args, **kwargs):
        with self._counter_lock:
            self._count += 1
            call_no = self._count
        if call_no == self._pause_call:
            self.paused.set()
            self.release.wait(timeout=JOIN_TIMEOUT)
        return self._real_replace(src, dst, *args, **kwargs)


class NotesConcurrencyTests(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix='constellation-notes-test-')
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)

    def _reap(self, threads, gate=None):
        # Registered after the rmtree cleanup, so it runs first (LIFO):
        # workers are released and joined before the temp dir disappears.
        if gate is not None:
            gate.release.set()
        for t in threads:
            t.join(JOIN_TIMEOUT)

    def _assert_finished(self, threads):
        self.assertFalse(any(t.is_alive() for t in threads),
                         'note worker hung past bounded join')

    def test_overlapping_appends_both_persist(self):
        gate = _ReplaceGate()
        errors = {}

        def worker(name):
            try:
                notes.append_note(self.data_dir, 'conv', name)
            except Exception as exc:  # surfaced via assertion below
                errors[name] = exc

        first = threading.Thread(target=worker, args=('alpha',), daemon=True)
        second = threading.Thread(target=worker, args=('beta',), daemon=True)
        self.addCleanup(self._reap, [first, second], gate)
        with patch.object(notes.os, 'replace', gate):
            first.start()
            self.assertTrue(gate.paused.wait(JOIN_TIMEOUT),
                            'first writer never reached os.replace')
            second.start()
            # Let the second writer either block on the lock (fixed) or race
            # ahead (broken), then let the first writer publish.
            second.join(0.5)
            gate.release.set()
            first.join(JOIN_TIMEOUT)
            second.join(JOIN_TIMEOUT)
        self._assert_finished([first, second])
        self.assertEqual(errors, {})
        persisted = notes.load_notes(self.data_dir).get('conv', [])
        self.assertEqual(sorted(n['text'] for n in persisted),
                         ['alpha', 'beta'],
                         'overlapping append_note calls lost a write')
        for note in persisted:
            self.assertEqual(len(note['note_id']), 8)

    def test_overlapping_delete_and_append_both_take_effect(self):
        notes.append_note(self.data_dir, 'conv', 'keep')
        doomed = notes.append_note(self.data_dir, 'conv', 'doomed')
        gate = _ReplaceGate()
        errors = {}
        deleted = []

        def deleter():
            try:
                deleted.append(notes.delete_note(
                    self.data_dir, 'conv', doomed['note_id']))
            except Exception as exc:
                errors['delete'] = exc

        def appender():
            try:
                notes.append_note(self.data_dir, 'conv', 'added')
            except Exception as exc:
                errors['append'] = exc

        t_delete = threading.Thread(target=deleter, daemon=True)
        t_append = threading.Thread(target=appender, daemon=True)
        self.addCleanup(self._reap, [t_delete, t_append], gate)
        with patch.object(notes.os, 'replace', gate):
            t_delete.start()
            self.assertTrue(gate.paused.wait(JOIN_TIMEOUT),
                            'deleter never reached os.replace')
            t_append.start()
            t_append.join(0.5)
            gate.release.set()
            t_delete.join(JOIN_TIMEOUT)
            t_append.join(JOIN_TIMEOUT)
        self._assert_finished([t_delete, t_append])
        self.assertEqual(errors, {})
        self.assertEqual(deleted, [True])
        persisted = notes.load_notes(self.data_dir).get('conv', [])
        self.assertEqual(sorted(n['text'] for n in persisted),
                         ['added', 'keep'],
                         'overlapping delete/append lost one of the writes')

    def test_concurrent_legacy_migration_preserves_new_note(self):
        conv_path = os.path.join(self.data_dir, 'conversations.json')
        with open(conv_path, 'w') as f:
            json.dump([{'id': 'legacy-conv',
                        'notes': [{'text': 'legacy note',
                                   'created_at': '2026-01-01T00:00:00Z'}]}],
                      f)
        gate = _ReplaceGate()
        errors = {}

        def migrator():
            try:
                notes.load_notes(self.data_dir)
            except Exception as exc:
                errors['load'] = exc

        def appender():
            try:
                notes.append_note(self.data_dir, 'conv', 'fresh')
            except Exception as exc:
                errors['append'] = exc

        t_load = threading.Thread(target=migrator, daemon=True)
        t_append = threading.Thread(target=appender, daemon=True)
        self.addCleanup(self._reap, [t_load, t_append], gate)
        with contextlib.redirect_stderr(io.StringIO()), \
                patch.object(notes.os, 'replace', gate):
            t_load.start()
            self.assertTrue(gate.paused.wait(JOIN_TIMEOUT),
                            'legacy migration never reached os.replace')
            t_append.start()
            t_append.join(0.5)
            gate.release.set()
            t_load.join(JOIN_TIMEOUT)
            t_append.join(JOIN_TIMEOUT)
        self._assert_finished([t_load, t_append])
        self.assertEqual(errors, {})
        persisted = notes.load_notes(self.data_dir)
        texts = [n['text'] for bucket in persisted.values() for n in bucket]
        self.assertIn('fresh', texts,
                      'legacy migration overwrote a just-added note')
        self.assertIn('legacy note', texts,
                      'legacy note lost during concurrent migration')

    def test_separate_process_appends_all_persist(self):
        script = (
            'import sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'from core import notes\n'
            'for i in range(5):\n'
            "    notes.append_note(sys.argv[2], 'conv',\n"
            "                      sys.argv[3] + '-' + str(i))\n"
        )
        procs = [subprocess.Popen(
                     [sys.executable, '-c', script,
                      REPO_ROOT, self.data_dir, tag],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for tag in ('p1', 'p2')]
        try:
            for proc in procs:
                self.assertEqual(proc.wait(timeout=30), 0,
                                 'append subprocess failed')
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        persisted = notes.load_notes(self.data_dir).get('conv', [])
        expected = sorted(f'{tag}-{i}' for tag in ('p1', 'p2')
                          for i in range(5))
        self.assertEqual(sorted(n['text'] for n in persisted), expected,
                         'cross-process appends lost a write')

    def _write_corrupt(self):
        path = os.path.join(self.data_dir, 'notes.json')
        with open(path, 'wb') as f:
            f.write(CORRUPT_BYTES)
        return path

    def test_load_notes_corrupt_returns_empty_and_quarantines(self):
        path = self._write_corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(notes.load_notes(self.data_dir), {})
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), CORRUPT_BYTES)
        quarantines = [p for p in os.listdir(self.data_dir)
                       if p.startswith('notes.json.corrupt.')]
        self.assertTrue(quarantines, 'corrupt notes.json was not quarantined')

    def test_append_to_corrupt_notes_raises_and_preserves_bytes(self):
        path = self._write_corrupt()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ValueError):
                notes.append_note(self.data_dir, 'conv', 'new note')
        with open(path, 'rb') as f:
            self.assertEqual(
                f.read(), CORRUPT_BYTES,
                'append_note replaced the bytes of an unparseable notes.json')

    def test_mutation_refused_when_quarantine_fails(self):
        path = self._write_corrupt()

        def failing_copy(src, dst, *args, **kwargs):
            raise OSError(13, 'Permission denied')

        with contextlib.redirect_stderr(io.StringIO()), \
                patch.object(notes.shutil, 'copy2', failing_copy):
            with self.assertRaises(ValueError):
                notes.append_note(self.data_dir, 'conv', 'new note')
        with open(path, 'rb') as f:
            self.assertEqual(
                f.read(), CORRUPT_BYTES,
                'a failed quarantine must never authorize overwriting')

    def test_malformed_top_level_shape_is_corruption(self):
        path = os.path.join(self.data_dir, 'notes.json')
        with open(path, 'w') as f:
            json.dump([1, 2, 3], f)  # valid JSON, wrong top-level shape
        with open(path, 'rb') as f:
            original = f.read()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(notes.load_notes(self.data_dir), {})
            with self.assertRaises(ValueError):
                notes.append_note(self.data_dir, 'conv', 'new note')
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), original)

    def test_replace_failure_leaves_original_and_cleans_temp(self):
        first = notes.append_note(self.data_dir, 'conv', 'first')
        path = os.path.join(self.data_dir, 'notes.json')
        with open(path, 'rb') as f:
            before = f.read()
        calls = []

        def failing_replace(src, dst, *args, **kwargs):
            calls.append((src, dst))
            raise OSError(28, 'No space left on device')

        with patch.object(notes.os, 'replace', failing_replace):
            with self.assertRaises(OSError):
                notes.append_note(self.data_dir, 'conv', 'second')

        self.assertTrue(calls, 'os.replace was never attempted; the failure '
                               'path was not exercised')
        with open(path, 'rb') as f:
            after = f.read()
        self.assertEqual(after, before,
                         'failed os.replace corrupted notes.json')
        data = json.loads(after)
        self.assertIn(first['note_id'],
                      [n.get('note_id') for n in data.get('conv', [])],
                      'previously persisted note lost after failed save')
        leftovers = [p for p in os.listdir(self.data_dir)
                     if p not in ('notes.json', 'notes.lock')]
        self.assertEqual(leftovers, [],
                         'temporary file left behind after failed replace')


class PipelineNotesTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix='constellation-pipeline-test-')
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.data_dir = os.path.join(base, 'data')
        os.makedirs(self.data_dir)
        self.source = os.path.join(base, 'synthetic-export.json')

    def _seed(self, conversations):
        vectors = np.array([[1.0, 0.0] for _ in conversations])
        indexer.save_pipeline_output(conversations, vectors, vectors,
                                     list(range(len(conversations))),
                                     _graph(conversations, 'old'),
                                     self.data_dir)

    def _run_pipeline(self, conversations, force=False, save_hook=None):
        data_path = self.data_dir
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launch, 'DATA_DIR', data_path))
            stack.enter_context(patch.object(
                launch, 'ensure_data_dir',
                lambda: os.makedirs(data_path, exist_ok=True)))
            stack.enter_context(patch.object(launch, 'save_config',
                                             lambda config: None))
            stack.enter_context(patch('core.embedder.Embedder', _FakeEmbedder))
            stack.enter_context(patch.object(indexer, 'build_clusters',
                                             lambda emb: {'k': 1}))
            stack.enter_context(patch.object(indexer, 'build_edges',
                                             lambda emb, convs: []))
            stack.enter_context(patch.object(
                indexer, 'build_graph_data',
                lambda convs, *args: _graph(convs, 'new')))
            if save_hook is not None:
                stack.enter_context(patch.object(
                    indexer, 'save_pipeline_output', save_hook))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            return launch.run_pipeline_embed(
                conversations, self.source, {'source': {}},
                force_reembed=force)

    def test_legacy_embedded_note_survives_rebuild(self):
        conv = _conv('a', 'hello world')
        legacy = dict(conv)
        legacy['notes'] = [{'text': 'legacy keepsake'}]
        self._seed([legacy])
        conv_path = os.path.join(self.data_dir, 'conversations.json')
        with open(conv_path, 'w') as fh:
            json.dump([legacy], fh)
        with open(conv_path) as fh:
            on_disk = json.load(fh)
        self.assertTrue(
            on_disk and on_disk[0].get('notes'),
            'precondition: legacy notes field missing from conversations.json')
        self.assertFalse(
            os.path.exists(os.path.join(self.data_dir, 'notes.json')),
            'precondition: no sidecar before rebuild')

        self._run_pipeline([conv], force=True)

        texts = [n.get('text')
                 for n in notes.load_notes(self.data_dir).get('a', [])]
        self.assertIn('legacy keepsake', texts,
                      'legacy note embedded in old conversations.json '
                      'was discarded by the rebuild')
        backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(self.data_dir)), 'backups')
        backup_texts = []
        if os.path.isdir(backup_dir):
            for name in os.listdir(backup_dir):
                if name.startswith('notes.json.bak.'):
                    with open(os.path.join(backup_dir, name)) as fh:
                        backup_texts.append(fh.read())
        self.assertTrue(
            any('legacy keepsake' in text for text in backup_texts),
            'no pre-rebuild notes backup contains the legacy note text')

    def test_note_deleted_during_rebuild_stays_deleted(self):
        convs = [_conv('a', 'hello world')]
        self._seed(convs)
        note = notes.append_note(self.data_dir, 'a', 'delete me')
        real_output = indexer.save_pipeline_output
        delete_result = []

        def save_then_user_deletes(*args, **kwargs):
            real_output(*args, **kwargs)
            delete_result.append(
                notes.delete_note(self.data_dir, 'a', note['note_id']))

        self._run_pipeline(convs, force=True,
                           save_hook=save_then_user_deletes)

        self.assertEqual(delete_result, [True],
                         'mid-rebuild delete_note did not succeed')
        remaining = notes.load_notes(self.data_dir).get('a', [])
        self.assertNotIn(note['note_id'],
                         [n.get('note_id') for n in remaining],
                         'note deleted during rebuild was resurrected '
                         'by the post-rebuild merge')


if __name__ == '__main__':
    unittest.main()
