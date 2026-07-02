"""Tests for the Phase 0 guardrail hooks (.claude/hooks/).

Every rule gets a synthetic stdin JSON: block cases must exit 2 with the
right stderr substring, allow cases must exit 0, and known false-positive
shapes (e.g. 'python' inside a commit message) must NOT trigger.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / '.claude' / 'hooks'
VPY = str(REPO / '.venv' / 'bin' / 'python')


def run_hook(script, event, project_dir=None):
    """Feed `event` to a hook on stdin; return (exit_code, stderr)."""
    env = dict(os.environ)
    env['CLAUDE_PROJECT_DIR'] = str(project_dir or REPO)
    proc = subprocess.run([VPY, str(HOOKS / script)], input=json.dumps(event),
                          capture_output=True, text=True, env=env, timeout=15)
    return proc.returncode, proc.stderr


def bash_event(command):
    return {'tool_name': 'Bash', 'tool_input': {'command': command}}


def file_event(tool, path):
    return {'tool_name': tool, 'tool_input': {'file_path': path}}


class TestGuardBashBlocks(unittest.TestCase):
    def assert_blocked(self, command, stderr_substr, project_dir=None):
        code, err = run_hook('guard_bash.py', bash_event(command), project_dir)
        self.assertEqual(code, 2, f'{command!r} should be BLOCKED, got {code}')
        self.assertIn(stderr_substr, err)

    def assert_allowed(self, command, project_dir=None):
        code, err = run_hook('guard_bash.py', bash_event(command), project_dir)
        self.assertEqual(code, 0, f'{command!r} should be ALLOWED, got {code}: {err}')

    # Rule 1: bare / system python and pip
    def test_bare_python_blocked(self):
        self.assert_blocked('python --version', '.venv/bin/python')

    def test_python3_blocked(self):
        self.assert_blocked('python3 launch.py', '.venv/bin/python')

    def test_versioned_python_blocked(self):
        self.assert_blocked('python3.13 -c "print(1)"', '.venv/bin/python')

    def test_pip_blocked(self):
        self.assert_blocked('pip install requests', '.venv/bin/python')

    def test_absolute_system_python_blocked(self):
        self.assert_blocked('/usr/bin/python3 script.py', '.venv/bin/python')

    def test_python_after_separator_blocked(self):
        self.assert_blocked('cd /tmp && python x.py', '.venv/bin/python')

    # Rule 2: rm on protected paths
    def test_rm_notes_blocked(self):
        self.assert_blocked('rm data/notes.json', 'protected')

    def test_rm_rf_data_blocked(self):
        self.assert_blocked('rm -rf data/', 'protected')

    def test_rm_backups_blocked(self):
        self.assert_blocked('rm -r backups/old', 'protected')

    # Rule 3: force git ops
    def test_force_push_blocked(self):
        self.assert_blocked('git push --force origin main', 'Force operations forbidden')

    def test_short_force_push_blocked(self):
        self.assert_blocked('git push -f', 'Force operations forbidden')

    def test_reset_hard_blocked(self):
        self.assert_blocked('git reset --hard HEAD~1', 'Force operations forbidden')

    # Rule 4: forbidden flag
    def test_dangerously_skip_permissions_blocked(self):
        self.assert_blocked('claude --dangerously-skip-permissions -p "hi"',
                            'Forbidden flag')

    # Rule 5: re-embed lock
    def test_reembed_blocked_without_flag(self):
        with tempfile.TemporaryDirectory() as td:  # no MERGE_SAFE flag here
            self.assert_blocked('.venv/bin/python launch.py --reembed',
                                'LOCKED', project_dir=td)

    def test_reembed_allowed_with_merge_safe_flag(self):
        with tempfile.TemporaryDirectory() as td:
            flags = Path(td) / '.claude' / 'flags'
            flags.mkdir(parents=True)
            (flags / 'MERGE_SAFE').touch()
            self.assert_allowed('.venv/bin/python launch.py --reembed',
                                project_dir=td)

    # Rule 6: branch creation
    def test_checkout_b_blocked(self):
        self.assert_blocked('git checkout -b feature/x', 'Main-only')

    def test_switch_c_blocked(self):
        self.assert_blocked('git switch -c feature/x', 'Main-only')


class TestGuardBashAllows(unittest.TestCase):
    def assert_allowed(self, command):
        code, err = run_hook('guard_bash.py', bash_event(command))
        self.assertEqual(code, 0, f'{command!r} should be ALLOWED, got {code}: {err}')

    def test_venv_python_allowed(self):
        self.assert_allowed('.venv/bin/python -m pytest tests/ -v')

    def test_uv_run_allowed(self):
        self.assert_allowed('uv run python script.py')

    def test_uv_pip_allowed(self):
        self.assert_allowed('uv pip install numpy')

    def test_commit_message_mentioning_python_not_blocked(self):
        self.assert_allowed('git commit -m "fix python detection"')

    def test_plain_push_allowed(self):
        self.assert_allowed('git push origin main')

    def test_plain_checkout_allowed(self):
        self.assert_allowed('git checkout main')

    def test_rm_unprotected_path_allowed(self):
        self.assert_allowed('rm /tmp/scratch.txt')

    def test_echo_containing_rm_of_notes_allowed(self):
        self.assert_allowed('echo "rm data/notes.json is forbidden"')

    def test_malformed_stdin_never_bricks(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(REPO))
        proc = subprocess.run([VPY, str(HOOKS / 'guard_bash.py')],
                              input='not json', capture_output=True,
                              text=True, env=env, timeout=15)
        self.assertEqual(proc.returncode, 0)


class TestGuardFiles(unittest.TestCase):
    def check(self, tool, path):
        return run_hook('guard_files.py', file_event(tool, path))

    def test_read_env_blocked(self):
        code, err = self.check('Read', '/Users/ama/constellation-v3/.env')
        self.assertEqual(code, 2)
        self.assertIn('.env', err)

    def test_nested_env_blocked(self):
        code, _ = self.check('Read', 'some/dir/.env')
        self.assertEqual(code, 2)

    def test_backups_blocked_all_tools(self):
        for tool in ('Read', 'Edit', 'Write'):
            code, err = self.check(tool, 'backups/notes.json.bak')
            self.assertEqual(code, 2, f'{tool} on backups/ should block')
            self.assertIn('backups/', err)

    def test_notes_json_edit_write_blocked(self):
        for tool in ('Edit', 'Write'):
            code, err = self.check(tool, 'data/notes.json')
            self.assertEqual(code, 2, f'{tool} on notes.json should block')
            self.assertIn('notes.json', err)

    def test_notes_json_read_allowed(self):
        code, _ = self.check('Read', 'data/notes.json')
        self.assertEqual(code, 0)

    def test_normal_file_allowed(self):
        for tool in ('Read', 'Edit', 'Write'):
            code, _ = self.check(tool, 'core/parser.py')
            self.assertEqual(code, 0)

    def test_env_substring_not_blocked(self):
        # config.yaml.example, .envrc-like names must not false-positive…
        code, _ = self.check('Read', 'docs/environment.md')
        self.assertEqual(code, 0)


class _TmpRepo:
    """Minimal committed git repo for stop_discipline tests."""
    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        def git(*args):
            subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                            *args], cwd=root, capture_output=True, check=True)
        git('init', '-q')
        (root / 'README').write_text('x')
        git('add', '.')
        git('commit', '-q', '-m', 'init')
        return root

    def __exit__(self, *exc):
        self.td.cleanup()


class TestStopDiscipline(unittest.TestCase):
    def run_stop(self, project_dir, extra_event=None):
        event = {'stop_hook_active': False, 'session_id': 'test'}
        event.update(extra_event or {})
        return run_hook('stop_discipline.py', event, project_dir)

    def test_stop_hook_active_short_circuits(self):
        with _TmpRepo() as root:
            (root / 'dirty.txt').write_text('uncommitted')  # would otherwise block
            code, _ = self.run_stop(root, {'stop_hook_active': True})
            self.assertEqual(code, 0)

    def test_uncommitted_changes_block(self):
        with _TmpRepo() as root:
            (root / 'dirty.txt').write_text('uncommitted')
            code, err = self.run_stop(root)
            self.assertEqual(code, 2)
            self.assertIn('Uncommitted changes', err)

    def test_untested_edits_block(self):
        with _TmpRepo() as root:
            flags = root / '.claude' / 'flags'
            flags.mkdir(parents=True)
            (flags / 'TESTED').touch()
            time.sleep(0.05)
            (flags / 'EDITED').touch()  # newer than TESTED
            # keep tree clean: flags live under .claude which is untracked ->
            # gitignore it inside the tmp repo
            (root / '.gitignore').write_text('.claude/\n.gitignore\n')
            code, err = self.run_stop(root)
            self.assertEqual(code, 2)
            self.assertIn("pytest hasn't run", err)

    def test_tested_after_edit_allows(self):
        with _TmpRepo() as root:
            flags = root / '.claude' / 'flags'
            flags.mkdir(parents=True)
            (flags / 'EDITED').touch()
            time.sleep(0.05)
            (flags / 'TESTED').touch()  # newer than EDITED
            (root / '.gitignore').write_text('.claude/\n.gitignore\n')
            code, err = self.run_stop(root)
            self.assertEqual(code, 0, err)

    def test_deferral_language_blocks(self):
        with _TmpRepo() as root:
            code, err = self.run_stop(root, {
                'last_assistant_message':
                    "The fix is in place. You'll need to run the migration yourself."})
            self.assertEqual(code, 2)
            self.assertIn('Deferral detected', err)

    def test_manual_steps_line_whitelisted(self):
        with _TmpRepo() as root:
            code, err = self.run_stop(root, {
                'last_assistant_message':
                    "Recorded in MANUAL_STEPS.md: you'll need to supply the API key."})
            self.assertEqual(code, 0, err)

    def test_clean_message_allows(self):
        with _TmpRepo() as root:
            code, err = self.run_stop(root, {
                'last_assistant_message': 'All groups complete; suite green; committed.'})
            self.assertEqual(code, 0, err)

    def test_placeholder_blocks(self):
        with _TmpRepo() as root:
            code, err = self.run_stop(root, {
                'last_assistant_message': 'I left a placeholder for the parser.'})
            self.assertEqual(code, 2)
            self.assertIn('Deferral detected', err)


class TestPostBash(unittest.TestCase):
    def run_post(self, project_dir, command, output):
        event = {'tool_name': 'Bash',
                 'tool_input': {'command': command},
                 'tool_response': {'stdout': output, 'stderr': ''}}
        return run_hook('post_bash.py', event, project_dir)

    def test_green_pytest_touches_tested_and_clears_edited(self):
        with tempfile.TemporaryDirectory() as td:
            flags = Path(td) / '.claude' / 'flags'
            flags.mkdir(parents=True)
            (flags / 'EDITED').touch()
            code, _ = self.run_post(td, '.venv/bin/python -m pytest tests/',
                                    '===== 101 passed in 9.12s =====')
            self.assertEqual(code, 0)
            self.assertTrue((flags / 'TESTED').exists())
            self.assertFalse((flags / 'EDITED').exists())

    def test_red_pytest_does_not_touch_tested(self):
        with tempfile.TemporaryDirectory() as td:
            flags = Path(td) / '.claude' / 'flags'
            flags.mkdir(parents=True)
            (flags / 'EDITED').touch()
            code, _ = self.run_post(td, '.venv/bin/python -m pytest tests/',
                                    '===== 1 failed, 100 passed in 9.12s =====')
            self.assertEqual(code, 0)
            self.assertFalse((flags / 'TESTED').exists())
            self.assertTrue((flags / 'EDITED').exists())

    def test_non_pytest_command_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            flags = Path(td) / '.claude' / 'flags'
            flags.mkdir(parents=True)
            code, _ = self.run_post(td, 'ls -la', 'total 0')
            self.assertEqual(code, 0)
            self.assertFalse((flags / 'TESTED').exists())


if __name__ == '__main__':
    unittest.main()
