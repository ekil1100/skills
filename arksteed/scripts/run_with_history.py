#!/usr/bin/env python3
"""Run an explicitly selected development command with persistent local history."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode(errors='surrogateescape').strip()


def create_run(root, head):
    state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    if not state_home.is_absolute():
        raise RuntimeError('XDG_STATE_HOME must be an absolute persistent path.')
    try:
        identity = root.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        identity = str(root)
    label = re.sub(r'[^A-Za-z0-9_-]', '-', root.name)[:32] or 'checkout'
    checkout = label + '-' + hashlib.sha256(os.fsencode(identity)).hexdigest()[:12]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = state_home / 'ark-runtime/runs' / checkout / f'{stamp}-{head[:12]}-{uuid.uuid4().hex[:8]}'
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'logs').mkdir()
    (directory / 'results/tmp').mkdir(parents=True)
    return directory


def save(directory, record):
    temporary = directory / 'run.json.tmp'
    temporary.write_text(json.dumps(record, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(directory / 'run.json')
    summary = ['# 运行记录', '', f'- 状态：{record["phase"]}',
               f'- 主机：`{record["host"]}`', f'- 源码目录：`{record["root"]}`',
               f'- 提交：`{record["head"]}`', f'- 开始时间：{record["started_at"]}',
               f'- 结束时间：{record.get("ended_at") or "尚未记录"}',
               f'- 退出码：{record.get("exit_code")}',
               '- [元数据](run.json) · [控制台日志](logs/command.log) · [原始结果](results/)',
               '', '## 命令', '', '```sh', record['command'], '```']
    if record.get('error'):
        summary += ['', '## 问题', '', record['error']]
    temporary = directory / 'summary.md.tmp'
    temporary.write_text('\n'.join(summary) + '\n')
    temporary.replace(directory / 'summary.md')


def write_log(stream, data):
    offset = 0
    while offset < len(data):
        count = stream.write(data[offset:])
        if not count:
            raise OSError('No progress writing command log.')
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


def run(root, text):
    root = Path(git(root, 'rev-parse', '--show-toplevel')).resolve()
    head = git(root, 'rev-parse', 'HEAD')
    directory = create_run(root, head)
    record = {'version': 1, 'kind': 'development-command', 'run_id': directory.name,
              'root': str(root), 'head': head, 'branch': git(root, 'branch', '--show-current'),
              'source_status': git(root, 'status', '--short', '--untracked-files=all'),
              'host': socket.gethostname(), 'command': text, 'phase': 'running',
              'started_at': now(), 'ended_at': None, 'exit_code': None}
    save(directory, record)
    display_output(sys.stdout.buffer, ('Run: ' + str(directory) + '\n').encode(errors='backslashreplace'))
    setup = ('cd -- "$1" || exit;\n'
             'export ARK_RUN_DIR="$2" ARK_RESULTS_DIR="$2/results" TMPDIR="$2/results/tmp"\n')
    process = None
    thread = None
    finished = threading.Event()
    errors = []
    try:
        with (directory / 'logs/command.log').open('xb', buffering=0) as log:
            def stream():
                try:
                    while True:
                        chunk = process.stdout.read1(8192)
                        if not chunk:
                            break
                        if not errors:
                            try:
                                write_log(log, chunk)
                            except OSError as exc:
                                errors.append('Command log write failed: ' + str(exc))
                        display_output(sys.stdout.buffer, chunk)
                except Exception as exc:
                    errors.append('Command output capture failed: ' + str(exc))
                finally:
                    try:
                        process.stdout.close()
                    finally:
                        finished.set()
            try:
                process = subprocess.Popen(['bash', '-lc', setup + text, 'development-run',
                                            str(root), str(directory)], stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                thread = threading.Thread(target=stream, daemon=True)
                thread.start()
                record['pid'] = process.pid
                save(directory, record)
                code = process.wait()
                # Interrupted Thread.join() can misreport a live capture thread as stopped.
                finished.wait()
                thread.join()
                record.update(exit_code=code, phase='passed' if code == 0 else 'failed')
            except BaseException:
                if process is not None:
                    stop_command(process, thread, finished)
                    record['exit_code'] = process.returncode
                raise
    except BaseException as exc:
        record.update(phase='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error=str(exc) or 'Interrupted; inspect the task before retrying.')
        raise
    finally:
        if process and process.stdout:
            process.stdout.close()
        if errors:
            record.update(phase='recording-failed', recording_error='; '.join(errors),
                          error='; '.join(errors))
        record['ended_at'] = now()
        save(directory, record)
    if record.get('recording_error'):
        display_output(sys.stderr.buffer,
                       ('History recording failed: ' + record['recording_error'] + '\n').encode(errors='backslashreplace'))
    display_output(sys.stdout.buffer, ('Command exit code: ' + str(code) + '\n').encode())
    return (code if code >= 0 else 128 - code) or (1 if record.get('recording_error') else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--command', required=True, help='Explicitly selected build/test shell command')
    args = parser.parse_args()
    try:
        return run(Path(args.repo), args.command)
    except (Exception, KeyboardInterrupt) as exc:
        display_output(sys.stderr.buffer,
                       ('Stopped: ' + (str(exc) or 'interrupted') + '\n').encode(errors='backslashreplace'))
        return 1


if __name__ == '__main__':
    sys.exit(main())
