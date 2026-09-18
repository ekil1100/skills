#!/usr/bin/env python3
"""Fixed remote worker; also imported locally for identical safety checks."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

PROTECTED = {'.git', '.agent', '.agents', '.pi', '.claude', 'CLAUDE.local.md'}


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
        if request.get('qemu'):
            command(['bash', '-lc', 'command -v qemu-aarch64-static'], root)
        # The command is explicitly supplied by the caller, never loaded as executable state.
        completed = subprocess.run(['bash', '-lc', 'cd -- "$1" || exit;\n' + request['command'],
                                    'remote-build', str(root)], stdout=sys.stderr, stderr=sys.stderr)
        return {'exit_code': completed.returncode, 'info': info(root),
                'dependencies': dependencies(root)}
    raise RuntimeError(f'Unknown remote action: {action}')


if __name__ == '__main__':
    try:
        response = worker(json.load(sys.stdin))
        print(json.dumps(response, ensure_ascii=True), flush=True)
    except Exception as exc:
        print('Remote operation failed: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
