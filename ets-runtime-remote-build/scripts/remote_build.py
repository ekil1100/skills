#!/usr/bin/env python3
"""Prepare, review, apply and run against the fixed SSH host work."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import uuid

import remote_ops as ops

HOST = 'work'
WORKER = Path(__file__).with_name('remote_ops.py')


def save(directory, state):
    temporary = directory / 'state.json.tmp'
    temporary.write_text(json.dumps(state, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(directory / 'state.json')


def refs(root, head):
    result = []
    remotes = ops.decode(ops.git(root, 'remote')).splitlines()
    records = ops.git(root, 'for-each-ref', '--format=%(refname) %(symref)',
                      '--points-at', head, 'refs/remotes/').splitlines()
    for record in records:
        ref, symbolic = ops.decode(record).split(' ', 1)
        if symbolic:
            continue
        for remote in remotes:
            specs = ops.decode(ops.git(root, 'config', '--get-all', f'remote.{remote}.fetch',
                                       allowed=(0, 1))).splitlines()
            for spec in specs:
                spec = spec.removeprefix('+')
                if ':' not in spec or spec.startswith('^'):
                    continue
                source, target = spec.split(':', 1)
                branch = None
                if target.count('*') == 1 and source.count('*') == 1:
                    prefix, suffix = target.split('*')
                    if ref.startswith(prefix) and ref.endswith(suffix):
                        middle = ref[len(prefix):len(ref) - len(suffix) if suffix else None]
                        branch = source.replace('*', middle)
                elif target == ref:
                    branch = source
                if branch and branch.startswith('refs/heads/'):
                    result.append({'branch': branch, 'url': ops.decode(
                        ops.git(root, 'remote', 'get-url', remote)).strip()})
    return result


def local_root(value):
    root = Path(ops.decode(ops.git(Path(value), 'rev-parse', '--show-toplevel')).strip())
    home = Path.home().resolve()
    relative = root.relative_to(home).as_posix()
    ops.safe_path(home, relative)
    if root.name != 'ets_runtime' or root.resolve() != root:
        raise RuntimeError('Expected a non-symlink ets_runtime repository under HOME.')
    ops.validate_index(root)
    ops.check_rsync()
    return root, relative


class Remote:
    def __init__(self, directory, state):
        self.directory = directory
        self.state = state
        self.uncertain = False
        self.token = uuid.uuid4().hex

    def request(self, action, **kwargs):
        request = {'action': action, 'relative': self.state['relative'],
                   'token': self.token, **kwargs}
        # Send the versioned worker directly, without generating or installing scripts.
        inner = 'exec python3 -c ' + shlex.quote(WORKER.read_text())
        argv = ['ssh', HOST, 'bash -lc ' + shlex.quote(inner)]
        data = json.dumps(request, ensure_ascii=True).encode()
        with (self.directory / 'remote.log').open('ab') as log:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            def drain():
                while True:
                    chunk = process.stderr.read1(8192)
                    if not chunk:
                        break
                    sys.stderr.buffer.write(chunk)
                    sys.stderr.buffer.flush()
                    log.write(chunk)
                    log.flush()
            thread = threading.Thread(target=drain, daemon=True)
            thread.start()
            try:
                process.stdin.write(data)
                process.stdin.close()
                output = process.stdout.read()
                code = process.wait()
                thread.join()
            except BaseException:
                self.uncertain = True
                process.terminate()
                process.wait()
                thread.join()
                raise
            finally:
                process.stdout.close()
                process.stderr.close()
        if code == 255 or (code and action == 'run'):
            self.uncertain = True
        if code:
            raise RuntimeError(f'Remote {action} failed ({code}); see {self.directory / "remote.log"}')
        try:
            return json.loads(output)
        except (ValueError, UnicodeError):
            self.uncertain = True
            raise RuntimeError('Invalid remote response; inspect the remote task before retrying.')

    @contextmanager
    def locked(self):
        acquired = False
        try:
            location = self.request('lock')
            acquired = True
            self.state['remote_root'] = location['root']
            self.state['remote_lock'] = location['lock']
            self.state['lock_token'] = self.token
            save(self.directory, self.state)
            yield self
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt) or self.state['phase'] in ('applying', 'running'):
                self.uncertain = True
            raise
        finally:
            if acquired and not self.uncertain:
                self.request('unlock')
            elif acquired:
                print('Remote task state is uncertain; lock retained: ' + self.state['remote_lock'],
                      file=sys.stderr)


def write_list(directory, names):
    path = directory / 'files.nul'
    path.write_bytes(b''.join(os.fsencode(n) + b'\0' for n in names))
    return path


def mirror(directory, state, dry=True):
    listing = directory / 'files.nul'
    expected = b''.join(os.fsencode(n) + b'\0' for n in state['local']['paths'])
    if listing.read_bytes() != expected:
        raise RuntimeError('Reviewed file list changed.')
    argv = ['rsync', '-lpc', '--protect-args', '--itemize-changes',
            '--out-format=%i|%n%L', '--from0', '--files-from=' + str(listing),
            '--delete-missing-args']
    if dry:
        argv.append('--dry-run')
    for name in sorted(ops.PROTECTED):
        argv.extend(['--exclude=/' + name, '--exclude=/' + name + '/***'])
    argv.extend(['--', state['root'] + '/', HOST + ':' + state['remote_root'] + '/'])
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with (directory / 'rsync.log').open('ab') as log:
        log.write(('DRY RUN\n' if dry else 'APPLY\n').encode() + result.stdout + result.stderr)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Rsync failed ({result.returncode}); see {directory / "rsync.log"}')
    return ops.decode(result.stdout)


def unchanged(root, expected):
    if ops.snapshot(root) != expected:
        raise RuntimeError('Local source or index changed; prepare and review again.')


def remote_snapshot(remote, state, check_ignored=True):
    result = remote.request('snapshot', paths=state['local']['paths'], check_ignored=check_ignored)
    if result['head'] != state['local']['head']:
        raise RuntimeError('Remote HEAD no longer matches the local baseline.')
    return result


def prepare(args):
    root, relative = local_root(args.repo)
    local = ops.snapshot(root)
    cache = Path.home() / '.cache' / 'ets-runtime-remote-build'
    cache.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='session-', dir=cache))
    print('Session: ' + str(directory), flush=True)
    state = {'version': 1, 'phase': 'preparing', 'root': str(root), 'relative': relative,
             'local': local, 'stashes': [], 'commands': [], 'ark_sync': 'skipped'}
    save(directory, state)
    try:
        with Remote(directory, state).locked() as remote:
            state['original'] = remote.request('inspect')
            save(directory, state)
            state['stashes'].append(remote.request('stash'))
            save(directory, state)
            state['aligned'] = remote.request('align', head=state['local']['head'],
                                              refs=refs(root, state['local']['head']))
            save(directory, state)
            unchanged(root, state['local'])
            state['remote_before'] = remote_snapshot(remote, state)
            if state['remote_before']['status']:
                raise RuntimeError('Remote worktree changed after alignment; prepare again.')
            write_list(directory, state['local']['paths'])
            state['preview'] = mirror(directory, state)
            unchanged(root, state['local'])
            if remote_snapshot(remote, state) != state['remote_before']:
                raise RuntimeError('Remote source changed during preview; prepare again.')
            (directory / 'preview.txt').write_text(state['preview'], errors='surrogateescape')
            state['phase'] = 'prepared'
            save(directory, state)
        print(state['preview'] or 'No source differences.')
        print('Review preview.txt and state.json, then invoke apply with this session.')
    except BaseException:
        state['phase'] = 'failed'
        save(directory, state)
        (directory / 'files.nul').unlink(missing_ok=True)
        raise


def apply(directory, state, remote):
    if state['phase'] != 'prepared':
        raise RuntimeError('Apply requires a prepared, reviewed session.')
    root, _ = local_root(state['root'])
    unchanged(root, state['local'])
    if remote_snapshot(remote, state) != state['remote_before']:
        raise RuntimeError('Remote source changed after review; prepare and review again.')
    if mirror(directory, state) != state['preview']:
        raise RuntimeError('Rsync preview changed after review; prepare and review again.')
    unchanged(root, state['local'])
    if remote_snapshot(remote, state) != state['remote_before']:
        raise RuntimeError('Remote source changed during recheck; prepare and review again.')
    state['phase'] = 'applying'
    save(directory, state)
    output = mirror(directory, state, dry=False)
    print(output or 'No source differences.')
    unchanged(root, state['local'])
    # Already-reviewed source may itself be ignored by the remote baseline index
    # (for example local git add -f). Protect old ignored files before writing;
    # after writing, enforce exact reviewed content instead.
    after = remote_snapshot(remote, state, check_ignored=False)
    if after['files'] != state['local']['files']:
        raise RuntimeError('Remote file contents or permissions do not match local source.')
    if mirror(directory, state):
        raise RuntimeError('Source differences remain after apply; build is blocked.')
    unchanged(root, state['local'])
    if remote_snapshot(remote, state, check_ignored=False) != after:
        raise RuntimeError('Remote source changed during verification.')
    state['remote_applied'] = after
    state['phase'] = 'applied'
    save(directory, state)
    (directory / 'files.nul').unlink(missing_ok=True)
    print('Source mirror verified. Build/test commands may now run.')


def run(directory, state, remote, args):
    if state['phase'] != 'applied':
        raise RuntimeError('Run requires a successfully applied session.')
    root, _ = local_root(state['root'])
    unchanged(root, state['local'])
    if remote_snapshot(remote, state, check_ignored=False) != state['remote_applied']:
        raise RuntimeError('Remote source changed after apply; prepare and review again.')
    record = {'command': args.command, 'qemu': args.qemu, 'exit_code': None}
    state['commands'].append(record)
    state['phase'] = 'running'
    save(directory, state)
    result = remote.request('run', command=args.command, qemu=args.qemu,
                            expected=state['remote_applied'])
    record.update(result)
    state['phase'] = 'applied'
    save(directory, state)
    print('Command exit code: ' + str(result['exit_code']))
    return result['exit_code'] if result['exit_code'] >= 0 else 128 - result['exit_code']


def existing(args):
    directory = Path(args.session).resolve()
    with (directory / 'session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((directory / 'state.json').read_text())
        if state.get('version') != 1:
            raise RuntimeError('Unsupported session format.')
        try:
            expected_phase = 'prepared' if args.action == 'apply' else 'applied'
            if state['phase'] != expected_phase:
                message = ('Apply requires a prepared, reviewed session.' if args.action == 'apply'
                           else 'Run requires a successfully applied session.')
                raise RuntimeError(message)
            with Remote(directory, state).locked() as remote:
                if args.action == 'apply':
                    apply(directory, state, remote)
                    return 0
                return run(directory, state, remote, args)
        except BaseException:
            state['phase'] = 'failed'
            save(directory, state)
            (directory / 'files.nul').unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    prep = subs.add_parser('prepare', help='Stash/align remote source and produce a reviewable preview')
    prep.add_argument('--repo', required=True)
    for action in ('apply', 'run'):
        sub = subs.add_parser(action)
        sub.add_argument('--session', required=True)
        if action == 'run':
            sub.add_argument('--command', required=True, help='Explicit shell command from the user/development skill')
            sub.add_argument('--qemu', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args)
            return 0
        return existing(args)
    except (Exception, KeyboardInterrupt) as exc:
        print('Stopped: ' + (str(exc) or 'interrupted'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
