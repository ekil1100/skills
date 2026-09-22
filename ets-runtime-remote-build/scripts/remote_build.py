#!/usr/bin/env python3
"""Prepare, review, apply and run against the fixed SSH host work."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import uuid

import remote_ops as ops

HOST = 'work'
WORKER = Path(__file__).with_name('remote_ops.py')


def save(directory, state):
    ops.save_run(directory, state)


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


def repository_root(value):
    root = Path(ops.decode(ops.git(Path(value), 'rev-parse', '--show-toplevel')).strip())
    home = Path.home().resolve()
    relative = root.relative_to(home).as_posix()
    ops.safe_path(home, relative)
    return root, relative


def local_root(value):
    root, relative = repository_root(value)
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
                   'token': self.token, 'run_id': self.state['run_id'],
                   'baseline': self.state['local']['head'], **kwargs}
        # Send the versioned worker directly, without generating or installing scripts.
        inner = 'exec python3 -c ' + shlex.quote(WORKER.read_text())
        argv = ['ssh', HOST, 'bash -lc ' + shlex.quote(inner)]
        data = json.dumps(request, ensure_ascii=True).encode()
        target = self.directory / 'logs' / 'remote.log'
        if action == 'run':
            target = self.directory / 'commands' / f'{kwargs["number"]:04d}' / 'logs' / 'command.log'
        capture_errors = []
        with target.open('ab', buffering=0) as log:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            def drain():
                try:
                    while True:
                        chunk = process.stderr.read1(8192)
                        if not chunk:
                            break
                        if not capture_errors:
                            try:
                                ops.write_all(log, chunk)
                            except OSError as exc:
                                capture_errors.append('Local remote-output log write failed: ' + str(exc))
                        try:
                            sys.stderr.buffer.write(chunk)
                            sys.stderr.buffer.flush()
                        except (BrokenPipeError, OSError):
                            pass
                except Exception as exc:
                    capture_errors.append('Remote output capture failed: ' + str(exc))
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
            raise RuntimeError(f'Remote {action} failed ({code}); see {target}')
        try:
            response = json.loads(output)
        except (ValueError, UnicodeError):
            self.uncertain = True
            raise RuntimeError('Invalid remote response; inspect the remote task before retrying.')
        if capture_errors:
            if action == 'run':
                response['recording_error'] = '; '.join(
                    [response['recording_error']] + capture_errors
                    if response.get('recording_error') else capture_errors)
            else:
                raise RuntimeError('; '.join(capture_errors))
        return response

    @contextmanager
    def locked(self):
        acquired = False
        try:
            location = self.request('lock')
            acquired = True
            self.state['remote_root'] = location['root']
            self.state['remote_lock'] = location['lock']
            self.state['remote_history'] = location['remote_history']
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
    label = ('preview-' if dry else 'apply-') + uuid.uuid4().hex[:8]
    output_path = directory / 'logs' / (label + '.stdout.log')
    error_path = directory / 'logs' / (label + '.stderr.log')
    # Both streams have durable file descriptors before rsync starts, including on interruption.
    with output_path.open('xb') as output, error_path.open('xb') as errors:
        result = subprocess.run(argv, stdout=output, stderr=errors)
    error = error_path.read_bytes()
    if error:
        sys.stderr.buffer.write(error)
    if result.returncode:
        raise RuntimeError(f'Rsync failed ({result.returncode}); see {error_path}')
    return ops.decode(output_path.read_bytes())


def unchanged(root, expected):
    if ops.snapshot(root) != expected:
        raise RuntimeError('Local source or index changed; prepare and review again.')


def remote_snapshot(remote, state, check_ignored=True):
    result = remote.request('snapshot', paths=state['local']['paths'], check_ignored=check_ignored)
    if result['head'] != state['local']['head']:
        raise RuntimeError('Remote HEAD no longer matches the local baseline.')
    return result


def prepare(args):
    root, relative = repository_root(args.repo)
    baseline = ops.info(root)
    directory = ops.create_history(relative, baseline['head'])
    print('Session: ' + str(directory), flush=True)
    state = {'version': 2, 'kind': 'remote-session', 'run_id': directory.name,
             'phase': 'preparing', 'root': str(root), 'relative': relative,
             'host': socket.gethostname(), 'started_at': ops.timestamp(),
             'source': baseline, 'stashes': [], 'commands': [], 'ark_sync': 'skipped'}
    save(directory, state)
    try:
        local_root(root)
        state['local'] = ops.snapshot(root)
        save(directory, state)
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
        print('Review preview.txt and run.json, then invoke apply with this session.')
    except BaseException as exc:
        state.update(phase='failed', error=str(exc) or 'Interrupted', ended_at=ops.timestamp())
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
    number = len(state['commands']) + 1
    command_dir = directory / 'commands' / f'{number:04d}'
    command_dir.mkdir(parents=True, mode=0o700)
    (command_dir / 'logs').mkdir()
    (command_dir / 'results').mkdir()
    record = {'version': 2, 'number': number, 'run_id': state['run_id'],
              'command': args.command, 'qemu': args.qemu, 'exit_code': None,
              'phase': 'dispatching', 'started_at': ops.timestamp(),
              'root': state['remote_root'], 'host': HOST,
              'remote_history': state['remote_history'] + f'/commands/{number:04d}'}
    state['commands'].append(record)
    state['phase'] = 'running'
    save(directory, state)
    save(command_dir, record)
    try:
        result = remote.request('run', command=args.command, qemu=args.qemu,
                                number=number, expected=state['remote_applied'])
        phase = 'passed' if result['exit_code'] == 0 else 'failed'
        record.update(result, phase='recording-failed' if result.get('recording_error') else phase)
        if result.get('recording_error'):
            record['error'] = result['recording_error']
    except BaseException as exc:
        record.update(phase='unknown', error=str(exc) or 'Interrupted; inspect remote task state.')
        raise
    finally:
        save(command_dir, record)
        save(directory, state)
    state['phase'] = 'applied'
    state['ended_at'] = ops.timestamp()
    save(directory, state)
    print('Remote command history: ' + record['remote_history'])
    print('Command exit code: ' + str(result['exit_code']))
    if result.get('recording_error'):
        print('History recording failed: ' + result['recording_error'], file=sys.stderr)
    return shell_code(result['exit_code']) or (1 if result.get('recording_error') else 0)


def existing(args):
    directory = Path(args.session).resolve()
    with (directory / 'session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((directory / 'run.json').read_text())
        if state.get('version') != 2 or state.get('kind') != 'remote-session':
            raise RuntimeError('Unsupported session format; archive old sessions and prepare a new run.')
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
        except BaseException as exc:
            state.update(phase='failed', error=str(exc) or 'Interrupted', ended_at=ops.timestamp())
            save(directory, state)
            (directory / 'files.nul').unlink(missing_ok=True)
            raise


def shell_code(code):
    return code if code >= 0 else 128 - code


def archive_history():
    base = Path.home() / '.cache' / 'ets-runtime-remote-build'
    archived = set()
    for manifest in ops.history_root().glob('*/*/run.json'):
        state = json.loads(manifest.read_text())
        if state.get('legacy_source'):
            archived.add(state['legacy_source'])
    for source in sorted(base.glob('session-*')):
        if str(source) in archived or source.is_symlink() or not (source / 'state.json').is_file():
            continue
        with (source / 'session.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('Skipped active legacy session: ' + str(source))
                continue
            old = json.loads((source / 'state.json').read_text())
            if old.get('phase') not in ('applied', 'failed'):
                print('Skipped unfinished legacy session: ' + str(source))
                continue
            if any(path.is_symlink() for path in source.rglob('*')):
                raise RuntimeError('Refusing legacy session containing symlinks: ' + str(source))
            directory = ops.create_history(old['relative'], old['local']['head'])
            shutil.copytree(source, directory / 'legacy')
            for original in source.rglob('*'):
                if original.is_file():
                    copy = directory / 'legacy' / original.relative_to(source)
                    if ops.hashlib.sha256(original.read_bytes()).digest() != ops.hashlib.sha256(copy.read_bytes()).digest():
                        raise RuntimeError('Legacy copy verification failed; source is preserved.')
            state = {'version': 2, 'kind': 'legacy-archive', 'run_id': directory.name,
                     'phase': old['phase'], 'root': old['root'], 'source': old['local'],
                     'started_at': ops.timestamp(), 'ended_at': ops.timestamp(),
                     'legacy_source': str(source), 'host': socket.gethostname()}
            save(directory, state)
            print('Archived: ' + str(source) + ' -> ' + str(directory))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    prep = subs.add_parser('prepare', help='Stash/align remote source and produce a reviewable preview')
    prep.add_argument('--repo', required=True)
    subs.add_parser('archive-history', help='Copy completed legacy cache sessions into verified persistent archives')
    for action in ('apply', 'run'):
        sub = subs.add_parser(action)
        sub.add_argument('--session', required=True)
        if action == 'run':
            sub.add_argument('--command', required=True, help='Explicit build/test shell command selected for this task')
            sub.add_argument('--qemu', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args)
            return 0
        if args.action == 'archive-history':
            return archive_history()
        return existing(args)
    except (Exception, KeyboardInterrupt) as exc:
        print('Stopped: ' + (str(exc) or 'interrupted'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
