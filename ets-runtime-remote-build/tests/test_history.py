"""Fault-injection tests for durable logging; all files are disposable fixtures."""
import errno
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import remote_build as build
import remote_ops as ops
sys.path.pop(0)


class HistoryFailureTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='history-fault-test-')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.directory = self.root / 'run'
        self.display = SimpleNamespace(buffer=io.BytesIO())

    def execute(self, text='printf "output\\n"; true'):
        with mock.patch.object(ops.sys, 'stderr', self.display):
            return ops.execute_recorded(self.root, self.directory, text, {'version': 2})

    def check_interrupt_cleanup(self, *, ignore_term=False, leader_exits=False, blocked_display=False):
        child_pid_file = self.root / 'child.pid'
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
        entry = ('import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                 'import remote_ops\n'
                 'try: remote_ops.execute_recorded(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],{})\n'
                 'except KeyboardInterrupt: sys.exit(130)\n')
        process = subprocess.Popen([sys.executable, '-B', '-c', entry, str(SCRIPTS),
                                    str(self.root), str(self.directory), command], text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        groups = {process.pid}

        def running(pid):
            try:
                return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1][0] not in 'ZX'
            except FileNotFoundError:
                return False

        try:
            logfile = self.directory / 'logs/command.log'
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
                    state = json.loads((self.directory / 'run.json').read_text())
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
            self.assertNotEqual(process.returncode, 0, stdout + stderr)
            self.assertFalse(running(child_pid), 'Interrupted command left a live descendant')
            state = json.loads((self.directory / 'run.json').read_text())
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
            process.stderr.close()

    def test_interrupt_waits_for_descendants_and_preserves_cleanup_log(self):
        self.check_interrupt_cleanup()

    def test_interrupt_kills_descendants_ignoring_termination(self):
        self.check_interrupt_cleanup(ignore_term=True)

    def test_interrupt_cleans_up_after_command_leader_exits(self):
        self.check_interrupt_cleanup(leader_exits=True)

    def test_interrupt_finishes_with_an_unread_display_pipe(self):
        self.check_interrupt_cleanup(blocked_display=True)

    def test_command_log_write_failure_is_not_a_successful_record(self):
        original = ops.write_all
        def broken(stream, data):
            if getattr(stream, 'name', '') == str(self.directory / 'logs/command.log'):
                raise OSError(errno.EIO, 'Injected log write failure')
            return original(stream, data)
        with mock.patch.object(ops, 'write_all', side_effect=broken):
            result = self.execute()
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['phase'], 'recording-failed')
        self.assertIn('Injected log write failure', result['recording_error'])
        self.assertEqual(json.loads((self.directory / 'run.json').read_text())['phase'], 'recording-failed')

    def test_child_file_limit_cannot_silently_truncate_parent_log(self):
        result = self.execute('ulimit -f 2; printf "%04096d" 0; true')
        self.assertEqual(result['phase'], 'passed')
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual((self.directory / 'logs/command.log').stat().st_size, 4096)

    def test_display_disconnect_does_not_invalidate_saved_log(self):
        display = mock.Mock()
        display.write.side_effect = BrokenPipeError('Disconnected display')
        self.display = SimpleNamespace(buffer=display)
        result = self.execute()
        self.assertEqual(result['phase'], 'passed')
        self.assertEqual((self.directory / 'logs/command.log').read_text(), 'output\n')

    def test_remote_operation_log_error_is_not_treated_as_display_loss(self):
        broken = mock.Mock()
        broken.write.side_effect = OSError(errno.ENOSPC, 'Injected operation-log failure')
        self.display = ops.LoggedStderr(self.display, broken)
        result = self.execute()
        self.assertEqual(result['phase'], 'recording-failed')
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('operation log write failed', result['recording_error'])
        self.assertEqual((self.directory / 'logs/command.log').read_text(), 'output\n')

    def test_local_ssh_capture_failure_preserves_real_command_exit(self):
        (self.directory / 'commands/0001/logs').mkdir(parents=True)
        state = {'relative': 'fixture/ets_runtime', 'run_id': 'test', 'local': {'head': 'abc'}}
        remote = build.Remote(self.directory, state)
        process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"exit_code": 0}'),
                                  stderr=io.BytesIO(b'original remote output'), wait=lambda: 0)
        with mock.patch.object(build.subprocess, 'Popen', return_value=process), \
             mock.patch.object(ops, 'write_all', side_effect=OSError(errno.EIO, 'Injected local log failure')), \
             mock.patch.object(build.sys, 'stderr', self.display):
            result = remote.request('run', number=1)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('Injected local log failure', result['recording_error'])
        self.assertFalse(remote.uncertain)

    def request(self):
        return {'relative': 'fixture/ets_runtime', 'run_id': 'fixed-run', 'baseline': 'aaa',
                'action': 'inspect', 'token': 'owner'}

    def test_remote_history_rejects_symlink_or_public_directory(self):
        history = self.root / 'history'
        target = self.root / 'outside'
        target.mkdir()
        history.symlink_to(target)
        with mock.patch.object(ops, 'remote_history', return_value=history):
            with self.assertRaisesRegex(RuntimeError, 'private owned directory'):
                ops.dispatch(self.request())
        self.assertEqual(list(target.iterdir()), [])
        history.unlink()
        history.mkdir(mode=0o755)
        with mock.patch.object(ops, 'remote_history', return_value=history):
            with self.assertRaisesRegex(RuntimeError, 'private owned directory'):
                ops.dispatch(self.request())

    def test_remote_history_rejects_different_baseline_without_overwrite(self):
        history = self.root / 'history'
        with mock.patch.object(ops, 'remote_history', return_value=history), \
             mock.patch.object(ops, 'worker', return_value={}), \
             mock.patch.object(ops.sys, 'stderr', self.display):
            ops.dispatch(self.request())
            original = (history / 'run.json').read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'identity does not match'):
                ops.dispatch(dict(self.request(), baseline='different'))
            self.assertEqual((history / 'run.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
