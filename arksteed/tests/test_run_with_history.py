"""Standalone tests for the development command history helper."""
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run_with_history.py'
spec = importlib.util.spec_from_file_location('development_history', SCRIPT)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


class DevelopmentHistoryTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='development-history-test-')
        self.addCleanup(fixture.cleanup)
        self.home = Path(fixture.name)
        self.root = self.home / 'ets_runtime'
        self.root.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_')}
        self.env.update(HOME=str(self.home), XDG_STATE_HOME=str(self.home / 'state'),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git('-c', 'init.templateDir=', 'init', '-b', 'main')
        self.git('config', 'core.hooksPath', os.devnull)
        self.git('config', 'commit.gpgsign', 'false')
        (self.root / 'source.txt').write_text('source\n')
        self.git('add', 'source.txt')
        self.git('commit', '-m', 'Fixture')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args],
                                       env=self.env, stderr=subprocess.PIPE)

    def invoke(self, text, script=SCRIPT):
        return subprocess.run([sys.executable, '-B', str(script), '--repo', str(self.root),
                               '--command', text], env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

    def directory(self, result):
        return Path(next(line[5:] for line in result.stdout.splitlines() if line.startswith('Run: ')))

    def test_copied_script_runs_without_any_sibling_package(self):
        standalone = self.home / 'standalone.py'
        shutil.copy2(SCRIPT, standalone)
        result = self.invoke('printf "saved" > "$ARK_RESULTS_DIR/result.txt"; printf "output\\n"', standalone)
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.directory(result)
        self.assertTrue(directory.is_relative_to(self.home / 'state/ark-runtime/runs'))
        self.assertEqual((directory / 'results/result.txt').read_text(), 'saved')
        self.assertEqual((directory / 'logs/command.log').read_text(), 'output\n')
        self.assertTrue((directory / 'summary.md').exists())

    def test_failure_exit_code_and_repeated_run_are_preserved(self):
        failed = self.invoke('printf "failed-output\\n"; exit 9')
        self.assertEqual(failed.returncode, 9)
        first = self.directory(failed)
        state = json.loads((first / 'run.json').read_text())
        self.assertEqual(state['phase'], 'failed')
        self.assertEqual(state['exit_code'], 9)
        passed = self.invoke('true')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotEqual(first, self.directory(passed))
        self.assertEqual((first / 'logs/command.log').read_text(), 'failed-output\n')

    def test_temporary_staging_uses_this_run(self):
        result = self.invoke('printf "%s" "$TMPDIR" > "$ARK_RESULTS_DIR/tmp-path.txt"')
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.directory(result)
        self.assertEqual((directory / 'results/tmp-path.txt').read_text(), str(directory / 'results/tmp'))

    def test_log_is_saved_before_command_finishes(self):
        marker = self.home / 'release'
        text = f'printf "started\\n"; while test ! -f "{marker}"; do sleep 0.02; done'
        process = subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--repo', str(self.root),
                                    '--command', text], env=self.env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            line = process.stdout.readline()
            self.assertTrue(line.startswith('Run: '), line)
            directory = Path(line[5:].strip())
            logfile = directory / 'logs/command.log'
            for _ in range(300):
                if logfile.exists() and logfile.read_text() == 'started\n':
                    break
                time.sleep(0.01)
            self.assertEqual(logfile.read_text(), 'started\n')
            self.assertIsNone(process.poll())
            self.assertEqual(json.loads((directory / 'run.json').read_text())['phase'], 'running')
            marker.touch()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            marker.touch()
            if process.poll() is None:
                process.communicate(timeout=10)
            process.stdout.close()
            process.stderr.close()

    def check_interrupt_cleanup(self, *, ignore_term=False, leader_exits=False,
                                blocked_display=False, merge_output=False):
        child_pid_file = self.home / 'child.pid'
        child_code = (
            'import os,signal,sys,time; from pathlib import Path\n'
            'def finish(*args):\n'
            ' print("cleanup-output", flush=True)\n'
            ' sys.exit(0)\n'
            f'signal.signal(signal.SIGTERM, signal.SIG_IGN if {ignore_term!r} else finish)\n'
            f'Path({str(child_pid_file)!r}).write_text(str(os.getpid()))\n'
            f'os.write(1, b"x" * 1048576) if {blocked_display!r} else None\n'
            'print("before", flush=True)\n'
            'while True: time.sleep(0.02)\n')
        command = shlex.join([sys.executable, '-u', '-c', child_code]) + ' &'
        if not leader_exits:
            command += ' wait'
        process = subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--repo', str(self.root),
                                    '--command', command], env=self.env, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT if merge_output else subprocess.PIPE,
                                   start_new_session=True)
        groups = {process.pid}

        def running(pid):
            try:
                return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1][0] not in 'ZX'
            except FileNotFoundError:
                return False

        try:
            line = process.stdout.readline()
            self.assertTrue(line.startswith('Run: '), line)
            directory = Path(line[5:].strip())
            logfile = directory / 'logs/command.log'
            for _ in range(400):
                if (child_pid_file.exists() and logfile.exists()
                        and (logfile.stat().st_size > 0 if blocked_display else 'before' in logfile.read_text())):
                    break
                time.sleep(0.01)
            child_pid = int(child_pid_file.read_text())
            groups.add(os.getpgid(child_pid))
            self.assertGreater(logfile.stat().st_size, 0)
            if leader_exits:
                for _ in range(400):
                    state = json.loads((directory / 'run.json').read_text())
                    if state.get('pid') and not running(state['pid']):
                        break
                    time.sleep(0.01)
                self.assertFalse(running(state['pid']))
            process.send_signal(signal.SIGINT)
            if blocked_display:
                for _ in range(900):
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(process.poll(), 'Cancellation blocked on an unread display pipe')
            stdout, stderr = process.communicate(timeout=12)
            self.assertNotEqual(process.returncode, 0, stdout + (stderr or ''))
            self.assertFalse(running(child_pid), 'Interrupted command left a live descendant')
            state = json.loads((directory / 'run.json').read_text())
            self.assertEqual(state['phase'], 'interrupted')
            self.assertIsNotNone(state['exit_code'])
            self.assertIsNotNone(state['ended_at'])
            if not ignore_term:
                self.assertIn('cleanup-output', logfile.read_text())
        finally:
            for group in groups:
                self.assertNotEqual(group, os.getpgrp())
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=5)
            process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_interrupt_waits_for_descendants_and_preserves_cleanup_log(self):
        self.check_interrupt_cleanup()

    def test_interrupt_kills_descendants_ignoring_termination(self):
        self.check_interrupt_cleanup(ignore_term=True)

    def test_interrupt_cleans_up_after_command_leader_exits(self):
        self.check_interrupt_cleanup(leader_exits=True)

    def test_interrupt_finishes_with_an_unread_display_pipe(self):
        self.check_interrupt_cleanup(blocked_display=True)

    def test_interrupt_finishes_with_an_unread_merged_display_pipe(self):
        self.check_interrupt_cleanup(blocked_display=True, merge_output=True)

    def test_log_failure_does_not_change_real_command_exit(self):
        output = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(history, 'write_log', side_effect=OSError(errno.EIO, 'Injected failure')), \
             mock.patch.object(history.sys, 'stdout', output), \
             mock.patch.object(history.sys, 'stderr', io.TextIOWrapper(io.BytesIO(), encoding='utf-8')):
            code = history.run(self.root, 'printf "output\\n"; true')
        self.assertEqual(code, 1)
        manifests = list((self.home / 'state/ark-runtime/runs').glob('*/*/run.json'))
        state = json.loads(manifests[0].read_text())
        self.assertEqual(state['exit_code'], 0)
        self.assertEqual(state['phase'], 'recording-failed')

    def test_invalid_state_root_is_rejected(self):
        self.env['XDG_STATE_HOME'] = 'relative-state'
        result = self.invoke('touch must-not-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'must-not-run').exists())


if __name__ == '__main__':
    unittest.main()
