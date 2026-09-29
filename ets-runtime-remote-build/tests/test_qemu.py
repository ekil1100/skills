"""Exercise QEMU preflight with an isolated executable search path."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import remote_ops as ops
sys.path.pop(0)


class QemuPreflightTests(unittest.TestCase):
    def run_case(self, binaries=(), *, qemu=True, version_exit=0):
        with tempfile.TemporaryDirectory(prefix='qemu-preflight-') as tmp:
            root = Path(tmp)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in binaries:
                executable = bin_dir / name
                executable.write_text(
                    '#!/bin/sh\n'
                    'test "$1" = --version || exit 99\n'
                    f'printf "%s\\n" "{name} test version"\n'
                    f'exit {version_exit}\n')
                executable.chmod(0o755)
            directory = root / 'run'
            popen = subprocess.Popen

            def isolated_popen(args, **kwargs):
                # Preserve the login-shell invocation, then isolate its final PATH.
                self.assertEqual(args[:2], ['bash', '-lc'])
                args = list(args)
                args[2] = 'export PATH=' + shlex.quote(str(bin_dir)) + '\n' + args[2]
                return popen(args, **kwargs)

            with mock.patch.object(ops.subprocess, 'Popen', side_effect=isolated_popen), \
                    mock.patch.object(ops.sys, 'stderr', SimpleNamespace(buffer=io.BytesIO())):
                ops.execute_recorded(root, directory, 'printf "COMMAND_RAN\\n"', {}, qemu=qemu)
            record = json.loads((directory / 'run.json').read_text())
            output = (directory / 'logs/command.log').read_text()
            return record['exit_code'], output

    def test_static_only(self):
        code, output = self.run_case(['qemu-aarch64-static'])
        self.assertEqual(code, 0)
        self.assertIn('qemu-aarch64-static test version', output)
        self.assertIn('COMMAND_RAN', output)

    def test_regular_only(self):
        code, output = self.run_case(['qemu-aarch64'])
        self.assertEqual(code, 0)
        self.assertIn('qemu-aarch64 test version', output)
        self.assertIn('COMMAND_RAN', output)

    def test_both_prefer_static(self):
        code, output = self.run_case(['qemu-aarch64-static', 'qemu-aarch64'])
        self.assertEqual(code, 0)
        self.assertIn('qemu-aarch64-static test version', output)
        self.assertNotIn('qemu-aarch64 test version', output)

    def test_missing_prevents_command(self):
        code, output = self.run_case()
        self.assertEqual(code, 127)
        self.assertIn('qemu-aarch64-static or qemu-aarch64 is required', output)
        self.assertNotIn('COMMAND_RAN', output)

    def test_broken_binary_prevents_command(self):
        code, output = self.run_case(['qemu-aarch64'], version_exit=9)
        self.assertEqual(code, 9)
        self.assertNotIn('COMMAND_RAN', output)

    def test_no_preflight_without_flag(self):
        code, output = self.run_case(qemu=False)
        self.assertEqual(code, 0)
        self.assertIn('COMMAND_RAN', output)
        self.assertNotIn('QEMU preflight:', output)


if __name__ == '__main__':
    unittest.main()
