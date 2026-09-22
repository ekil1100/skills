#!/usr/bin/env python3
"""Fixed remote worker; also imported locally for identical safety checks."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

PROTECTED = {'.git', '.agent', '.agents', '.pi', '.claude', 'CLAUDE.local.md'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def history_root():
    value = os.environ.get('XDG_STATE_HOME')
    root = Path(value) if value else Path.home() / '.local' / 'state'
    if not root.is_absolute():
        raise RuntimeError('XDG_STATE_HOME must be an absolute persistent path.')
    return root / 'ark-runtime' / 'runs'


def checkout_id(relative):
    label = re.sub(r'[^A-Za-z0-9_-]', '-', Path(relative).name)[:32] or 'checkout'
    return label + '-' + hashlib.sha256(os.fsencode(relative)).hexdigest()[:12]


def create_history(relative, head):
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_id = f'{name}-{head[:12]}-{uuid.uuid4().hex[:8]}'
    directory = history_root() / checkout_id(relative) / run_id
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'logs').mkdir()
    (directory / 'results').mkdir()
    return directory


def save_run(directory, state):
    state['updated_at'] = timestamp()
    temporary = directory / 'run.json.tmp'
    temporary.write_text(json.dumps(state, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(directory / 'run.json')
    # This index is derived from run.json; raw logs and older commands are never replaced.
    lines = ['# 运行记录', '', f'- 状态：{state.get("phase", "unknown")}',
             f'- 运行 ID：`{state.get("run_id", directory.name)}`',
             f'- 主机：`{state.get("host", socket.gethostname())}`',
             f'- 源码目录：`{state.get("root", "unknown")}`',
             f'- 开始时间：{state.get("started_at", "unknown")}',
             f'- 结束时间：{state.get("ended_at") or "尚未记录"}',
             '- 元数据：[run.json](run.json)', '- 日志目录：[logs/](logs/)',
             '- 原始结果：[results/](results/)', '']
    if 'command' in state:
        lines += ['## 命令', '', '```sh', state['command'], '```',
                  f'退出码：{state.get("exit_code")}', '']
    if state.get('error'):
        lines += ['## 错误', '', state['error'], '']
    if state.get('remote_history'):
        lines += ['## 远端记录', '', '`work:' + state['remote_history'] + '`', '']
    if state.get('commands'):
        lines += ['## 命令历史', '']
        for entry in state['commands']:
            number = entry['number']
            lines.append(f'- [{number:04d}](commands/{number:04d}/summary.md)：'
                         f'{entry.get("phase", "见命令记录")}，退出码 {entry.get("exit_code")}')
    if state.get('legacy_source'):
        lines += ['## 历史归档', '', '原记录：`' + state['legacy_source'] + '`；原文件保存在 `legacy/`，未删除源记录。']
    summary = directory / 'summary.md.tmp'
    summary.write_text('\n'.join(lines) + '\n')
    summary.replace(directory / 'summary.md')


class HistoryWriteError(RuntimeError):
    pass


def write_all(stream, data):
    offset = 0
    while offset < len(data):
        count = stream.write(data[offset:])
        if not count:
            raise OSError('No progress writing durable log.')
        offset += count
    stream.flush()


def display_output(stream, data):
    # Console forwarding is best-effort; the durable log is written separately.
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = None
    if not isinstance(descriptor, int):
        try:
            stream.write(data)
            stream.flush()
        except (BrokenPipeError, OSError):
            pass
        return
    blocking = None
    try:
        blocking = os.get_blocking(descriptor)
        os.set_blocking(descriptor, False)
        offset = 0
        while offset < len(data):
            count = os.write(descriptor, data[offset:])
            if not count:
                break
            offset += count
    except OSError:
        pass
    finally:
        if blocking is not None:
            try:
                os.set_blocking(descriptor, blocking)
            except OSError:
                pass


def stop_command(process, thread, finished):
    # The command owns a separate session. Never signal the recorder's group.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        deadline = time.monotonic() + 5
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        if thread and thread.ident is not None:
            finished.wait(timeout=max(0, deadline - time.monotonic()))
        # The leader may already be gone while descendants still own the pipe.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if thread and thread.ident is not None:
            finished.wait()
            thread.join()
    finally:
        signal.signal(signal.SIGINT, previous)


def execute_recorded(root, directory, text, metadata, qemu=False):
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    (directory / 'logs').mkdir(exist_ok=True)
    (directory / 'results' / 'tmp').mkdir(parents=True, exist_ok=True)
    logfile = directory / 'logs' / 'command.log'
    # Exclusive creation prevents accidentally replaying a command into an old record.
    with logfile.open('xb', buffering=0) as output:
        record = {**metadata, 'root': str(root), 'host': socket.gethostname(),
                  'command': text, 'qemu': qemu, 'started_at': timestamp(),
                  'ended_at': None, 'phase': 'running', 'exit_code': None,
                  'log_path': str(logfile), 'results_dir': str(directory / 'results')}
        save_run(directory, record)
        setup = ('cd -- "$1" || exit;\n'
                 'export ARK_RUN_DIR="$2" ARK_RESULTS_DIR="$2/results" TMPDIR="$2/results/tmp"\n')
        if qemu:
            setup += 'command -v qemu-aarch64-static || exit 127\n'
        errors = []
        finished = threading.Event()
        def stream():
            sink_failed = False
            try:
                while True:
                    chunk = process.stdout.read1(8192)
                    if not chunk:
                        break
                    if not sink_failed:
                        try:
                            write_all(output, chunk)
                        except OSError as exc:
                            sink_failed = True
                            errors.append('Command log write failed: ' + str(exc))
                    try:
                        display_output(sys.stderr.buffer, chunk)
                    except HistoryWriteError as exc:
                        if not errors:
                            errors.append(str(exc))
                    except (BrokenPipeError, OSError):
                        # Display/SSH failure is distinct from the durable sink above.
                        pass
            except Exception as exc:
                errors.append('Command output capture failed: ' + str(exc))
            finally:
                try:
                    process.stdout.close()
                finally:
                    finished.set()
        process = None
        thread = None
        try:
            process = subprocess.Popen(['bash', '-lc', setup + text, 'ark-run',
                                        str(root), str(directory)], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            thread = threading.Thread(target=stream, daemon=True)
            thread.start()
            record['pid'] = process.pid
            save_run(directory, record)
            code = process.wait()
            # Interrupted Thread.join() can misreport a live capture thread as stopped.
            finished.wait()
            thread.join()
            record.update(exit_code=code, phase='passed' if code == 0 else 'failed')
        except BaseException as exc:
            if process is not None:
                stop_command(process, thread, finished)
                record['exit_code'] = process.returncode
            record.update(phase='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                          error=str(exc) or 'Interrupted; task state requires inspection.')
            raise
        finally:
            if process and process.stdout:
                process.stdout.close()
            if errors:
                record.update(phase='recording-failed', recording_error='; '.join(errors),
                              error='; '.join(errors))
            record['ended_at'] = timestamp()
            save_run(directory, record)
    return record


def command(argv, cwd=None, data=None, allowed=(0,)):
    result = subprocess.run(argv, cwd=cwd, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode not in allowed:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv!r}\n"
                           + os.fsdecode(result.stderr))
    return result.stdout


def git(root, *args, data=None, allowed=(0,)):
    return command(['git', *args], root, data, allowed)


def decode(data):
    return os.fsdecode(data)


def safe_path(root, name):
    parts = name.split('/')
    if not name or any(p in ('', '.', '..') for p in parts) or name.startswith('/'):
        raise RuntimeError(f"Unsafe relative path: {name!r}")
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise RuntimeError(f"Unsafe path parent: {str(current)!r}")
    return root / name


def protected(name):
    return name.split('/')[0] in PROTECTED


def validate_index(root):
    flags = git(root, 'ls-files', '-v', '-z').split(b'\0')
    if any(r and (r[:1] == b'S' or r[:1].islower()) for r in flags):
        raise RuntimeError('Sparse checkout and assume-unchanged entries are not supported.')
    if git(root, 'ls-files', '--unmerged', '-z'):
        raise RuntimeError('Unmerged index entries are not supported.')
    if any(r.startswith(b'160000 ') for r in git(root, 'ls-files', '--stage', '-z').split(b'\0')):
        raise RuntimeError('Submodules are not supported.')


def check_rsync():
    # New rsync releases list --secluded-args in help but still accept --protect-args.
    command(['rsync', '--from0', '--delete-missing-args', '--protect-args', '--version'])


def paths(root, head):
    tree = git(root, 'ls-tree', '-r', '-z', head)
    index = git(root, 'ls-files', '--stage', '-z')
    for record in tree.split(b'\0') + index.split(b'\0'):
        if record and record.startswith(b'160000 '):
            raise RuntimeError('Submodules are not supported.')
    if git(root, 'ls-files', '--unmerged', '-z'):
        raise RuntimeError('Unmerged index entries are not supported.')
    baseline = git(root, 'ls-tree', '-r', '--name-only', '-z', head)
    current = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    return [decode(p) for p in sorted(set((baseline + current).split(b'\0')) - {b''})
            if not protected(decode(p))]


def fingerprint(root, names):
    records = []
    for name in names:
        path = safe_path(root, name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            records.append([name, 'missing'])
            continue
        if stat.S_ISLNK(info.st_mode):
            records.append([name, 'link', os.readlink(path)])
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            records.append([name, 'file', stat.S_IMODE(info.st_mode), digest.hexdigest()])
        else:
            raise RuntimeError(f"Directories and special files are not supported: {name!r}")
    return records


def snapshot(root, names=None):
    validate_index(root)
    head = decode(git(root, 'rev-parse', 'HEAD')).strip()
    if names is None:
        names = paths(root, head)
    return {'head': head, 'paths': names, 'files': fingerprint(root, names),
            'status': decode(git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')),
            'index': hashlib.sha256(git(root, 'ls-files', '--stage', '-z')).hexdigest()}


def info(root):
    return {'branch': decode(git(root, 'branch', '--show-current')).strip() or '(detached)',
            'head': decode(git(root, 'rev-parse', 'HEAD')).strip(),
            'status': decode(git(root, 'status', '--short', '--untracked-files=all'))}


def check_hidden_changes(root):
    # Git's stat/fsmonitor cache may call changed bytes clean. Stash only saves
    # changes Git notices, so hash supposedly unchanged files before it can reset
    # anything. hash-object applies the same clean/EOL filters as the index.
    known = set(git(root, 'diff-files', '--name-only', '-z').split(b'\0'))
    regular = []
    for record in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        metadata, raw_name = record.split(b'\t', 1)
        mode, oid, _ = metadata.split()
        if raw_name in known:
            continue
        name = decode(raw_name)
        path = safe_path(root, name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise RuntimeError(f'Git did not report a missing tracked file: {name!r}')
        if mode == b'120000' and stat.S_ISLNK(info.st_mode):
            actual = git(root, 'hash-object', '--stdin', data=os.fsencode(os.readlink(path))).strip()
            if actual != oid:
                raise RuntimeError(f'Git did not report changed tracked content: {name!r}')
        elif mode in (b'100644', b'100755') and stat.S_ISREG(info.st_mode):
            regular.append((raw_name, oid))
        else:
            raise RuntimeError(f'Git did not report a tracked file type change: {name!r}')
    if regular:
        # --stdin-paths accepts Git C-quoted paths, including newline/non-UTF8 names.
        listing = b''.join(b'"' + b''.join(
            b'\\%03o' % byte if byte < 32 or byte > 126 or byte in (34, 92)
            else bytes([byte]) for byte in name) + b'"\n' for name, _ in regular)
        actual = git(root, 'hash-object', '--stdin-paths', data=listing).splitlines()
        if len(actual) != len(regular):
            raise RuntimeError('Failed to hash the full tracked file list.')
        for (name, expected), digest in zip(regular, actual):
            if digest != expected:
                raise RuntimeError(f'Git did not report changed tracked content: {decode(name)!r}')


def stash(root):
    from datetime import datetime, timezone
    validate_index(root)
    # Stash restores HEAD/index paths before any rsync preview. Protect ignored
    # replacements here, including directories replacing formerly tracked files.
    tree = git(root, 'ls-tree', '-r', '-z', 'HEAD')
    if any(r.startswith(b'160000 ') for r in tree.split(b'\0')):
        raise RuntimeError('Submodules are not supported.')
    tracked = git(root, 'ls-tree', '-r', '--name-only', '-z', 'HEAD')
    tracked += git(root, 'ls-files', '--cached', '-z')
    names = [decode(p) for p in sorted(set(tracked.split(b'\0')) - {b''})]
    check_ignored(root, names)
    check_hidden_changes(root)
    before = info(root)
    result = {'before': before, 'stash': None}
    # Emit the original state before any mutation, including on failure.
    print('Original remote state: ' + json.dumps(before), file=sys.stderr, flush=True)
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        message = 'ets-runtime-remote-build pre-build ' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        old = git(root, 'rev-parse', '--verify', 'refs/stash', allowed=(0, 128)).strip()
        failure = None
        try:
            git(root, 'stash', 'push', '--include-untracked', '-m', message)
        except RuntimeError as exc:
            failure = exc
        new = git(root, 'rev-parse', '--verify', 'refs/stash', allowed=(0, 128)).strip()
        if new and new != old:
            result['stash'] = {'id': decode(new), 'message': message}
            print('Saved stash (not restored): ' + json.dumps(result), file=sys.stderr, flush=True)
        if failure:
            raise failure
        if not result['stash']:
            raise RuntimeError('Stash did not create a new commit.')
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Remote worktree is not clean after stash.')
    return result


def check_ignored(root, names):
    # Check all listed existing destinations, including unchanged ones, conservatively.
    existing = [name for name in names if os.path.lexists(safe_path(root, name))]
    if existing:
        ignored = git(root, 'check-ignore', '--stdin', '-z',
                      data=b''.join(os.fsencode(n) + b'\0' for n in existing), allowed=(0, 1))
        if ignored:
            raise RuntimeError('Remote ignored destination conflict: ' + repr(decode(ignored)))
    fingerprint(root, names)


def dependencies(root):
    base = root.parent.parent
    candidates = [base / 'build']
    for folder in ('arkcompiler', 'third_party'):
        parent = base / folder
        if parent.is_dir():
            candidates.extend(p for p in parent.iterdir() if p.is_dir() and not p.is_symlink())
    result = {}
    for path in sorted(set(candidates)):
        if path == root or not path.is_dir():
            continue
        top = git(path, 'rev-parse', '--show-toplevel', allowed=(0, 128)).strip()
        if top and Path(decode(top)).resolve() == path.resolve():
            result[str(path)] = info(path)
    return result


def mapped_root(relative):
    home = Path.home().resolve()
    path = safe_path(home, relative)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('Remote repository path is missing or is a symlink.')
    if path.name != 'ets_runtime' or path.resolve() != path:
        raise RuntimeError('Expected a non-symlink ets_runtime repository under HOME.')
    if Path(decode(git(path, 'rev-parse', '--show-toplevel')).strip()).resolve() != path:
        raise RuntimeError('Remote path is not a repository root.')
    return path


def worker(request):
    root = mapped_root(request['relative'])
    lock = Path.home() / '.cache' / 'ets-runtime-remote-build' / (
        hashlib.sha256(os.fsencode(str(root))).hexdigest() + '.lock')
    action = request['action']
    if action == 'lock':
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            raise RuntimeError(f'Remote operation locked; inspect running tasks before recovery: {lock}')
        (lock / 'owner').write_text(request['token'])
        return {'root': str(root), 'lock': str(lock)}
    if not lock.is_dir() or (lock / 'owner').read_text() != request['token']:
        raise RuntimeError('Remote operation lock is not owned by this invocation.')
    if action == 'unlock':
        (lock / 'owner').unlink()
        lock.rmdir()
        return {}
    if action == 'inspect':
        check_rsync()
        return {'info': info(root), 'dependencies': dependencies(root)}
    if action == 'stash':
        return stash(root)
    if action == 'align':
        validate_index(root)
        head = request['head']
        if decode(git(root, 'rev-parse', 'HEAD')).strip() != head:
            urls = {}
            for remote in decode(git(root, 'remote')).splitlines():
                url = decode(git(root, 'remote', 'get-url', remote)).strip()
                urls.setdefault(url, remote)
            ref = next((r for r in request['refs'] if r['url'] in urls), None)
            if ref is None:
                raise RuntimeError('No matching shared branch with an identical configured remote URL.')
            git(root, 'fetch', '--no-tags', '--', urls[ref['url']], ref['branch'])
            git(root, 'cat-file', '-e', head + '^{commit}')
            git(root, 'merge-base', '--is-ancestor', head, 'FETCH_HEAD')
            if git(root, 'status', '--porcelain', '--untracked-files=all'):
                raise RuntimeError('Remote changed during alignment; prepare again to stash and recheck.')
            git(root, 'switch', '--no-overwrite-ignore', '--detach', head)
        if decode(git(root, 'rev-parse', 'HEAD')).strip() != head:
            raise RuntimeError('Remote commit alignment failed.')
        return info(root)
    if action == 'snapshot':
        if request.get('check_ignored', True):
            check_ignored(root, request['paths'])
        return snapshot(root, request['paths'])
    if action == 'run':
        if snapshot(root, request['expected']['paths']) != request['expected']:
            raise RuntimeError('Remote source changed before command execution.')
        number = request['number']
        if type(number) is not int or number < 1:
            raise RuntimeError('Invalid command number.')
        directory = remote_history(request) / 'commands' / f'{number:04d}'
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        # The command is explicitly supplied by the caller, never loaded as executable state.
        result = execute_recorded(root, directory, request['command'],
                                  {'version': 2, 'run_id': request['run_id'], 'number': number,
                                   'source': request['expected'], 'dependencies': dependencies(root)},
                                  qemu=request.get('qemu', False))
        return {'exit_code': result['exit_code'], 'info': info(root),
                'dependencies': result['dependencies'], 'remote_command_dir': str(directory),
                'started_at': result['started_at'], 'ended_at': result['ended_at'],
                'recording_error': result.get('recording_error')}
    raise RuntimeError(f'Unknown remote action: {action}')


def remote_history(request):
    run_id = request['run_id']
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id):
        raise RuntimeError('Invalid run ID.')
    return history_root() / checkout_id(request['relative']) / run_id


class LoggedStderr:
    def __init__(self, original, log):
        self.original, self.log = original, log
        self.buffer = self

    def write(self, value):
        data = value.encode(errors='backslashreplace') if isinstance(value, str) else value
        try:
            write_all(self.log, data)
        except OSError as exc:
            raise HistoryWriteError('Remote operation log write failed: ' + str(exc)) from exc
        display_output(self.original.buffer, data)
        return len(value)

    def flush(self):
        self.log.flush()


def dispatch(request):
    directory = remote_history(request)
    directory.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        directory.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        details = directory.lstat()
        if (not stat.S_ISDIR(details.st_mode) or stat.S_IMODE(details.st_mode) & 0o077
                or details.st_uid != os.geteuid()):
            raise RuntimeError('Remote history directory must be a private owned directory, not a symlink.')
    manifest = directory / 'run.json'
    expected = {'version': 2, 'kind': 'remote-session', 'run_id': request['run_id'],
                'relative': request['relative'], 'root': str(Path.home() / request['relative']),
                'head': request.get('baseline')}
    if created:
        state = {**expected, 'host': socket.gethostname(), 'started_at': timestamp(), 'commands': []}
    else:
        if manifest.is_symlink() or not manifest.is_file():
            raise RuntimeError('Remote history manifest is missing or unsafe.')
        state = json.loads(manifest.read_text())
        if any(state.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Remote history identity does not match this session.')
    for name in ('logs', 'results'):
        child = directory / name
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_symlink() or not child.is_dir():
            raise RuntimeError('Unsafe remote history subdirectory.')
    if (directory / 'logs' / 'operations.log').is_symlink():
        raise RuntimeError('Unsafe remote operation log.')
    state.update(last_action=request['action'], phase='running')
    save_run(directory, state)
    previous = sys.stderr
    with (directory / 'logs' / 'operations.log').open('ab', buffering=0) as log:
        sys.stderr = LoggedStderr(previous, log)
        try:
            print(f'{timestamp()} {request["action"]}', file=sys.stderr)
            response = worker(request)
            state['phase'] = 'failed' if state.get('error') else 'completed'
            if request['action'] == 'lock':
                response['remote_history'] = str(directory)
            elif request['action'] == 'inspect':
                state['environment'] = response
            elif request['action'] == 'stash':
                state.setdefault('stashes', []).append(response)
            elif request['action'] == 'run':
                state['commands'].append({'number': request['number'], **response})
            state['ended_at'] = timestamp()
            save_run(directory, state)
            return response
        except BaseException as exc:
            state.update(phase='failed', error=str(exc), ended_at=timestamp())
            print('Remote operation failed: ' + str(exc), file=sys.stderr)
            save_run(directory, state)
            raise
        finally:
            sys.stderr = previous


if __name__ == '__main__':
    try:
        response = dispatch(json.load(sys.stdin))
        print(json.dumps(response, ensure_ascii=True), flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        display_output(sys.stderr.buffer,
                       ('Remote operation failed: ' + (str(exc) or 'interrupted') + '\n').encode(errors='backslashreplace'))
        sys.exit(1)
