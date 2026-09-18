#!/usr/bin/env python3
"""Opt-in real-SSH smoke test. Creates isolated fixtures, never uses real runtime trees."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

PREFIX = '.cache/ets-runtime-remote-build-smoke'


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode().strip()


def put(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def setup(root, remote=False):
    root.mkdir(parents=True, exist_ok=False)
    git(root, '-c', 'init.templateDir=', 'init', '--object-format=sha1', '-b', 'master')
    for key, value in [('user.name', 'Remote Build Smoke'), ('user.email', 'smoke@example.invalid'),
                       ('core.hooksPath', '/dev/null'), ('commit.gpgsign', 'false'),
                       ('core.autocrlf', 'false')]:
        git(root, 'config', key, value)
    put(root, '.gitignore', '/out/\n/.pi/\n')
    for name in ('source.txt', 'staged-delete.txt', 'unstaged-delete.txt'):
        put(root, name, 'baseline\n')
    git(root, 'add', '--', '.gitignore', 'source.txt', 'staged-delete.txt', 'unstaged-delete.txt')
    # Construct a deterministic fixture object: host commit wrappers may rewrite
    # dates/messages even with commit hooks disabled. This only seeds a new repo.
    tree = git(root, 'write-tree')
    identity = 'Remote Build Smoke <smoke@example.invalid> 946684800 +0000'
    commit = f'tree {tree}\nauthor {identity}\ncommitter {identity}\n\nSmoke fixture baseline\n'
    result = subprocess.run(['git', '-C', str(root), 'hash-object', '-t', 'commit', '-w', '--stdin'],
                            input=commit, text=True, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    git(root, 'update-ref', 'refs/heads/master', result.stdout.strip(), '0' * 40)
    if remote:
        put(root, 'source.txt', 'remote work to stash\n')
        put(root, 'remote notes.txt', 'remote untracked work\n')
        put(root, 'out/preserved.bin', 'remote artifact\n')
        put(root, '.pi/settings.json', 'remote metadata\n')
        put(root, '.git/smoke-marker', 'remote git metadata\n')
    return {'root': str(root), 'head': git(root, 'rev-parse', 'HEAD')}


def remote_action(request):
    relative = Path(request['relative'])
    check(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe fixture path.')
    check(relative.parts[:2] == ('.cache', 'ets-runtime-remote-build-smoke')
          and len(relative.parts) == 4 and relative.name == 'ets_runtime'
          and relative.parts[2].startswith('smoke-'), 'Unexpected fixture path.')
    root = Path.home() / relative
    check(root.resolve() == root, 'Symlink fixture paths are not supported.')
    if request['action'] == 'setup':
        return setup(root, remote=True)
    check(request['action'] == 'verify', 'Unknown smoke action.')
    check(git(root, 'rev-parse', 'HEAD') == request['head'], 'Remote baseline changed.')
    for name, content in {
        'source.txt': 'local updated source\n',
        'space and\nnewline.txt': 'new source\n',
        'staged-new.txt': 'staged source\n',
        'out/preserved.bin': 'remote artifact\n',
        'out/smoke-result.txt': 'smoke-ok\n',
        '.pi/settings.json': 'remote metadata\n',
        '.git/smoke-marker': 'remote git metadata\n',
    }.items():
        check((root / name).read_text() == content, f'Unexpected remote contents: {name!r}')
    for name in ('staged-delete.txt', 'unstaged-delete.txt', 'remote notes.txt', 'out/local.bin'):
        check(not (root / name).exists(), f'Unexpected remote path: {name!r}')
    saved = request['stash']
    check(git(root, 'stash', 'list', '--format=%H') == saved, 'Saved stash was changed.')
    check(git(root, 'show', saved + ':source.txt') == 'remote work to stash', 'Stash lost tracked work.')
    check(git(root, 'show', saved + '^3:remote notes.txt') == 'remote untracked work',
          'Stash lost untracked work.')
    return {'root': str(root), 'head': git(root, 'rev-parse', 'HEAD'),
            'stash': saved, 'status': git(root, 'status', '--short'),
            'artifact': str(root / 'out/smoke-result.txt')}


def remote(action, relative, **fields):
    source = Path(__file__).read_text()
    command = 'exec python3 -c ' + shlex.quote(source) + ' --remote'
    completed = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'work',
                                'bash -lc ' + shlex.quote(command)],
                               input=json.dumps(dict(action=action, relative=relative, **fields)),
                               text=True, stdout=subprocess.PIPE, check=True)
    return json.loads(completed.stdout)


def invoke(runner, *args, expected=0):
    result = subprocess.run([sys.executable, '-B', str(runner), *args],
                            stdout=subprocess.PIPE, text=True)
    print(result.stdout, end='', flush=True)
    check(result.returncode == expected,
          f'Unexpected {args[0]} exit code: {result.returncode}, expected {expected}')
    return result.stdout


def smoke():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-work', action='store_true', required=True,
                        help='Explicitly allow real SSH and isolated fixture creation on work')
    parser.parse_args()
    base = Path.home() / PREFIX
    base.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix='smoke-', dir=base))
    root = fixture / 'ets_runtime'
    relative = root.relative_to(Path.home()).as_posix()
    print('Local fixture: ' + str(root), flush=True)
    local = setup(root)
    distant = remote('setup', relative)
    print('Remote fixture: ' + distant['root'], flush=True)
    check(local['head'] == distant['head'], 'Fixture commit IDs differ; no source will be synced.')
    put(root, 'source.txt', 'local updated source\n')
    put(root, 'space and\nnewline.txt', 'new source\n')
    put(root, 'staged-new.txt', 'staged source\n')
    git(root, 'add', '--', 'staged-new.txt')
    git(root, 'rm', '--', 'staged-delete.txt')
    (root / 'unstaged-delete.txt').unlink()
    put(root, 'out/local.bin', 'ignored local artifact\n')
    put(root, '.pi/settings.json', 'local metadata\n')
    runner = Path(__file__).resolve().parents[1] / 'scripts/remote_build.py'
    output = invoke(runner, 'prepare', '--repo', str(root))
    sessions = [line.removeprefix('Session: ') for line in output.splitlines()
                if line.startswith('Session: ')]
    check(len(sessions) == 1, 'Missing session path.')
    session = Path(sessions[0])
    state = json.loads((session / 'state.json').read_text())
    # Review this deterministic fixture's exact transfer/delete set, not just counts.
    reviewed = {}
    for row in state['preview'].splitlines():
        fields = row.split('|', 1)
        check(len(fields) == 2, 'Unexpected rsync preview record: ' + repr(row))
        item, name = fields
        check(name not in reviewed, 'Duplicate preview path: ' + repr(name))
        reviewed[name] = item
    check(set(reviewed) == {'source.txt', 'space and\\#012newline.txt', 'staged-new.txt',
                            'staged-delete.txt', 'unstaged-delete.txt'},
          'Unexpected fixture preview: ' + repr(reviewed))
    check({name for name, item in reviewed.items() if item.startswith('*deleting')}
          == {'staged-delete.txt', 'unstaged-delete.txt'}, 'Unexpected deletion set.')
    saved = state['stashes'][0]['stash']['id']
    print('Reviewed expected source changes and two deletions.', flush=True)
    invoke(runner, 'apply', '--session', str(session))
    invoke(runner, 'run', '--session', str(session), '--command',
           'test "$(cat source.txt)" = "local updated source" && '
           'mkdir -p out && printf "smoke-ok\\n" > out/smoke-result.txt && '
           'printf "Remote smoke command succeeded.\\n"')
    invoke(runner, 'run', '--session', str(session), '--command', 'exit 7', expected=7)
    final = json.loads((session / 'state.json').read_text())
    check(final['phase'] == 'applied', 'Command failure invalidated the source mirror.')
    check([entry['exit_code'] for entry in final['commands']] == [0, 7], 'Wrong command results.')
    check(final['ark_sync'] == 'skipped', 'Unexpected automatic dependency sync.')
    verification = remote('verify', relative, head=local['head'], stash=saved)
    report = {'result': 'passed', 'local_fixture': str(root), 'session': str(session),
              **verification, 'command_exit_codes': [0, 7], 'ark_sync': 'skipped'}
    (fixture / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    print('Smoke passed. Isolated fixtures, stash and logs are retained for inspection.', flush=True)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--remote']:
            print(json.dumps(remote_action(json.load(sys.stdin))), flush=True)
        else:
            smoke()
    except Exception as exc:
        print('Smoke failed: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
