"""Offline CLI integration tests using disposable repositories and real rsync.

The actual remote worker is executed unchanged over fake SSH. Real rsync handles
all capability checks, previews and transfers without a help-text adapter.
Run: python3 -B -m unittest discover -s ets-runtime-remote-build/tests -v
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "remote_build.py"
WORKER = RUNNER.with_name("remote_ops.py")
TOOLS = {name: shutil.which(name) for name in ("git", "rsync", "bash", "python3")}
PROTECTED = (".agent", ".agents", ".pi", ".claude", "CLAUDE.local.md")


@unittest.skipUnless(all(TOOLS.values()), "git, rsync, bash and python3 are required")
class RemoteBuildIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="remote-build-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "local home"
        self.remote_home = self.base / "remote home"
        self.bin = self.base / "bin"
        for path in (self.home, self.remote_home, self.bin):
            path.mkdir()
        self.relative = Path("ohos/tree/arkcompiler/ets_runtime")
        self.local = self.home / self.relative
        self.remote = self.remote_home / self.relative
        self.ssh_log = self.base / "ssh.jsonl"
        self.ark_log = self.base / "ark.jsonl"
        # Ignore host Git configuration, alternate worktrees, SSH/rsync overrides,
        # shell startup files and user hooks. Git transport is restricted to files.
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "SSH_", "RSYNC_"))
            and key not in ("BASH_ENV", "ENV", "CDPATH", "SHELLOPTS", "BASHOPTS")
            and not key.startswith("BASH_FUNC_")
        }
        self.env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", os.defpath),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_CONFIG_KEY_1": "init.templateDir",
            "GIT_CONFIG_VALUE_1": "",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Integration Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Integration Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "LC_ALL": "C",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "FAKE_REMOTE_HOME": str(self.remote_home),
            "FAKE_SSH_LOG": str(self.ssh_log),
            "FAKE_ARK_LOG": str(self.ark_log),
        })
        self.make_executable("ssh", """
            import json
            import os
            import shlex
            import subprocess
            import sys

            args = sys.argv[1:]
            if not args or args.pop(0) != 'work':
                sys.exit('Fake SSH only accepts work; real SSH is never invoked.')
            command = shlex.split(args[0]) if len(args) == 1 else args
            if not (command[:2] == ['bash', '-lc'] or
                    command[:2] == ['rsync', '--server']):
                sys.exit('Unexpected fake SSH command: ' + repr(command))
            with open(os.environ['FAKE_SSH_LOG'], 'a') as log:
                log.write(json.dumps(command) + '\\n')
            os.environ['HOME'] = os.environ['FAKE_REMOTE_HOME']
            os.environ['XDG_CONFIG_HOME'] = os.environ['HOME'] + '/.config'
            os.environ['XDG_CACHE_HOME'] = os.environ['HOME'] + '/.cache'
            os.chdir(os.environ['HOME'])
            if command[:2] == ['bash', '-lc'] and os.environ.get('FAKE_SSH_BREAK_RUN'):
                data = sys.stdin.buffer.read()
                if json.loads(data)['action'] == 'run':
                    sys.exit(255)
                sys.exit(subprocess.run(command, input=data).returncode)
            os.execvpe(command[0], command, os.environ)
        """)
        # The worker starts another bash -lc for run. Strip login mode there as
        # well as in the outer SSH command, otherwise Bash resets temporary HOME.
        self.make_executable("bash", f"""
            import os
            import sys

            args = [('-c' if arg == '-lc' else arg)
                    for arg in sys.argv[1:] if arg != '-l']
            os.execv({TOOLS['bash']!r}, ['bash', *args])
        """)
        self.make_executable("ark", """
            import json
            import os
            import sys

            with open(os.environ['FAKE_ARK_LOG'], 'a') as log:
                log.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),
                                      'home': os.environ['HOME']}) + '\\n')
            print('Fake ark: ' + ' '.join(sys.argv[1:]), flush=True)
            sys.exit(23 if sys.argv[1:] == ['build', 'fail'] else 0)
        """)
        self.local.mkdir(parents=True)
        self.git(self.local, "init", "-b", "master")
        self.put(self.local, ".gitignore", "/out/\n" + "".join(
            "/" + name + "\n" for name in PROTECTED))
        for name in ("source.txt", "staged-delete.txt", "unstaged-delete.txt",
                     "delete space\nline.txt", "src/nested.txt", "mode.sh"):
            self.put(self.local, name, "baseline\n")
        self.git(self.local, "add", ".")
        self.git(self.local, "commit", "-m", "Initial fixture")
        self.remote.parent.mkdir(parents=True)
        self.checked([TOOLS["git"], "clone", "--no-local", str(self.local), str(self.remote)])
        self.head = self.git(self.local, "rev-parse", "HEAD").strip()

    def make_executable(self, name, body):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body).lstrip())
        path.chmod(0o755)

    def invoke(self, argv, *, data=None, env=None):
        return subprocess.run(
            [str(arg) for arg in argv], cwd=self.home, env=env or self.env,
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
        )

    def checked(self, argv):
        result = self.invoke(argv)
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        return result.stdout

    def git(self, root, *args):
        self.assertTrue(root.is_relative_to(self.base), "Git must stay in the fixture")
        return self.checked([TOOLS["git"], "-C", root, *args])

    @staticmethod
    def put(root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    @staticmethod
    def diagnostic(result):
        return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def cli(self, action, *, session=None, command=None):
        args = [sys.executable, RUNNER, action]
        args += ["--repo", self.local] if action == "prepare" else ["--session", session]
        if command is not None:
            args += ["--command", command]
        return self.invoke(args)

    def prepare(self, *, succeeds=True, error=None):
        result = self.cli("prepare")
        if succeeds:
            self.assertEqual(result.returncode, 0, self.diagnostic(result))
        else:
            self.assertNotEqual(result.returncode, 0, self.diagnostic(result))
        if error is not None:
            self.assertIn(error, result.stderr, self.diagnostic(result))
        sessions = [line.removeprefix("Session: ") for line in result.stdout.splitlines()
                    if line.startswith("Session: ")]
        session = Path(sessions[0]) if sessions else None
        if succeeds:
            self.assertIsNotNone(session, self.diagnostic(result))
            self.assertEqual(self.state(session)["phase"], "prepared")
            self.assertTrue((session / "preview.txt").is_file())
            self.assertTrue((session / "files.nul").is_file())
        return session

    @staticmethod
    def state(session):
        return json.loads((session / "state.json").read_text())

    @staticmethod
    def records(path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def apply(self, session):
        result = self.cli("apply", session=session)
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        self.assertEqual(self.state(session)["phase"], "applied")
        self.assertFalse((session / "files.nul").exists())

    def assert_run_blocked(self, session):
        marker = self.remote_home / "must-not-run"
        result = self.cli("run", session=session,
                          command="touch " + shlex.quote(str(marker)))
        self.assertNotEqual(result.returncode, 0, self.diagnostic(result))
        self.assertIn("Run requires a successfully applied session", result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.state(session)["commands"], [])

    def assert_apply_rejected(self, session, error):
        result = self.cli("apply", session=session)
        self.assertNotEqual(result.returncode, 0, self.diagnostic(result))
        self.assertIn(error, result.stderr, self.diagnostic(result))
        self.assertEqual(self.state(session)["phase"], "failed")
        self.assertFalse((session / "files.nul").exists())
        self.assert_run_blocked(session)

    def test_prepare_accepts_real_rsync_supported_options(self):
        self.checked([TOOLS["rsync"], "--protect-args", "--version"])
        self.prepare()

    def test_worker_inspect_accepts_real_rsync_supported_options(self):
        env = dict(self.env, HOME=str(self.remote_home))
        request = {"relative": self.relative.as_posix(), "token": "test-owner"}

        def worker(action):
            return self.invoke([sys.executable, "-B", WORKER],
                               data=json.dumps(dict(request, action=action)), env=env)

        locked = worker("lock")
        self.assertEqual(locked.returncode, 0, self.diagnostic(locked))
        try:
            result = worker("inspect")
            self.assertEqual(result.returncode, 0, self.diagnostic(result))
            self.assertEqual(json.loads(result.stdout)["info"]["head"], self.head)
        finally:
            unlocked = worker("unlock")
            self.assertEqual(unlocked.returncode, 0, self.diagnostic(unlocked))

    def test_prepare_apply_run_mirrors_source_without_touching_metadata_or_artifacts(self):
        self.put(self.local, "source.txt", "staged content\n")
        self.git(self.local, "add", "source.txt")
        self.put(self.local, "source.txt", "final worktree content\n")
        self.put(self.local, "new space\nline.txt", "new untracked content\n")
        self.put(self.local, "new directory/deep/file.txt", "nested addition\n")
        self.put(self.local, "staged-new.txt", "new indexed content\n")
        self.git(self.local, "add", "staged-new.txt")
        self.git(self.local, "rm", "--", "staged-delete.txt", "delete space\nline.txt")
        (self.local / "unstaged-delete.txt").unlink()
        (self.local / "mode.sh").chmod(0o755)
        (self.local / "source-link").symlink_to("source.txt")
        # A tracked source must not disappear merely because a new rule ignores it.
        with (self.local / ".gitignore").open("a") as stream:
            stream.write("/source.txt\n")
        metadata = [".git/test-sentinel"] + [
            name if name == "CLAUDE.local.md" else name + "/settings.txt"
            for name in PROTECTED
        ]
        for name in metadata:
            self.put(self.local, name, "local metadata\n")
            self.put(self.remote, name, "remote metadata\n")
        self.put(self.local, "out/local-only.bin", "not source\n")
        self.put(self.remote, "out/remote-only.bin", "keep remote artifact\n")
        self.put(self.remote, ".git/info/exclude", "/src/generated.bin\n")
        self.put(self.remote, "src/generated.bin", "keep artifact beside source\n")
        original_index = self.git(self.local, "ls-files", "--stage", "-z")
        original_status = self.git(self.local, "status", "--porcelain=v1", "-z")
        remote_index = self.git(self.remote, "ls-files", "--stage", "-z")
        session = self.prepare()
        state = self.state(session)
        self.assertEqual(state["remote_root"], str(self.remote))
        self.assertEqual(state["original"]["info"]["head"], self.head)
        self.assertEqual(state["aligned"]["branch"], "master")
        self.assertIsNone(state["stashes"][0]["stash"])
        listing = (session / "files.nul").read_bytes().split(b"\0")
        for name in ("new space\nline.txt", "delete space\nline.txt", "staged-delete.txt",
                     "unstaged-delete.txt", "source.txt"):
            self.assertIn(os.fsencode(name), listing)
        for name in metadata + ["out/local-only.bin"]:
            self.assertNotIn(os.fsencode(name), listing)
        self.assertIn("*deleting", (session / "preview.txt").read_text())
        self.assertEqual((self.remote / "source.txt").read_text(), "baseline\n")
        self.assertTrue((self.remote / "staged-delete.txt").exists())
        self.assertFalse((self.remote / "staged-new.txt").exists())
        self.apply(session)
        for name, content in {
            "source.txt": "final worktree content\n",
            "new space\nline.txt": "new untracked content\n",
            "new directory/deep/file.txt": "nested addition\n",
            "staged-new.txt": "new indexed content\n",
        }.items():
            self.assertEqual((self.remote / name).read_text(), content)
        for name in ("staged-delete.txt", "unstaged-delete.txt", "delete space\nline.txt"):
            self.assertFalse((self.remote / name).exists())
        self.assertEqual((self.remote / "mode.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual(os.readlink(self.remote / "source-link"), "source.txt")
        for name in metadata:
            self.assertEqual((self.remote / name).read_text(), "remote metadata\n")
        self.assertEqual((self.remote / "out/remote-only.bin").read_text(), "keep remote artifact\n")
        self.assertFalse((self.remote / "out/local-only.bin").exists())
        self.assertEqual((self.remote / "src/generated.bin").read_text(),
                         "keep artifact beside source\n")
        self.assertEqual((self.remote / ".git/info/exclude").read_text(), "/src/generated.bin\n")
        self.assertEqual(self.git(self.remote, "ls-files", "--stage", "-z"), remote_index)
        self.assertEqual(self.git(self.local, "ls-files", "--stage", "-z"), original_index)
        self.assertEqual(self.git(self.local, "status", "--porcelain=v1", "-z"), original_status)
        result = self.cli("run", session=session,
                          command="ark build && printf 'built\\n' > out/result.txt")
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        self.assertEqual((self.remote / "out/result.txt").read_text(), "built\n")
        self.assertEqual(self.records(self.ark_log), [
            {"args": ["build"], "cwd": str(self.remote), "home": str(self.remote_home)}])
        self.assertTrue(any(call[:2] == ["rsync", "--server"]
                            for call in self.records(self.ssh_log)))
        self.assertEqual(self.state(session)["commands"][0]["exit_code"], 0)
        self.assertEqual(self.state(session)["ark_sync"], "skipped")
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), self.head)
        self.assertFalse(Path(self.state(session)["remote_lock"]).exists())

    def test_remote_stashes_are_retained_and_never_restored(self):
        self.put(self.remote, "source.txt", "older saved work\n")
        self.git(self.remote, "stash", "push", "-m", "Pre-existing stash")
        older = self.git(self.remote, "rev-parse", "refs/stash").strip()
        self.put(self.remote, "source.txt", "remote staged work\n")
        self.git(self.remote, "add", "source.txt")
        self.put(self.remote, "source.txt", "remote unstaged work\n")
        self.put(self.remote, "remote new\nfile.txt", "remote untracked work\n")
        self.put(self.remote, "out/keep.bin", "ignored artifact\n")
        self.put(self.local, "source.txt", "local replacement\n")
        session = self.prepare()
        stash = self.state(session)["stashes"][0]
        saved = stash["stash"]["id"]
        self.assertEqual(len(saved), len(self.head))
        self.assertNotEqual(saved, older)
        self.assertEqual(stash["before"]["branch"], "master")
        self.assertEqual(stash["before"]["head"], self.head)
        self.assertIn("Saved stash (not restored)", (session / "remote.log").read_text())
        self.assertEqual(self.git(self.remote, "show", saved + ":source.txt"), "remote unstaged work\n")
        self.assertEqual(self.git(self.remote, "show", saved + "^2:source.txt"), "remote staged work\n")
        self.assertEqual(self.git(self.remote, "show", saved + "^3:remote new\nfile.txt"),
                         "remote untracked work\n")
        self.assertEqual(self.git(self.remote, "status", "--porcelain"), "")
        self.apply(session)
        result = self.cli("run", session=session, command="ark build")
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        self.assertEqual(self.git(self.remote, "stash", "list", "--format=%H").splitlines(),
                         [saved, older])
        self.assertEqual((self.remote / "source.txt").read_text(), "local replacement\n")
        self.assertFalse((self.remote / "remote new\nfile.txt").exists())
        self.assertEqual((self.remote / "out/keep.bin").read_text(), "ignored artifact\n")

    def test_stash_is_retained_when_prepare_later_fails(self):
        self.put(self.remote, "source.txt", "remote work to retain\n")
        self.put(self.remote, "remote-untracked.txt", "untracked work to retain\n")
        self.put(self.remote, ".git/info/exclude", "/collision.txt\n")
        self.put(self.remote, "collision.txt", "ignored destination\n")
        self.put(self.local, "collision.txt", "local source\n")
        session = self.prepare(succeeds=False, error="Remote ignored destination conflict")
        state = self.state(session)
        self.assertEqual(state["phase"], "failed")
        saved = state["stashes"][0]["stash"]["id"]
        self.assertEqual(self.git(self.remote, "rev-parse", "refs/stash").strip(), saved)
        self.assertEqual(self.git(self.remote, "show", saved + ":source.txt"),
                         "remote work to retain\n")
        self.assertEqual(self.git(self.remote, "show", saved + "^3:remote-untracked.txt"),
                         "untracked work to retain\n")
        self.assertEqual((self.remote / "source.txt").read_text(), "baseline\n")
        self.assertFalse((self.remote / "remote-untracked.txt").exists())
        self.assertEqual((self.remote / "collision.txt").read_text(), "ignored destination\n")
        self.assertIn(saved, (session / "remote.log").read_text())
        self.assertFalse(Path(state["remote_lock"]).exists())
        self.assert_run_blocked(session)

    def test_existing_ignored_destination_is_rejected_even_when_content_matches(self):
        for content in ("remote artifact\n", "local source\n"):
            with self.subTest(remote_content=content):
                self.put(self.local, "collision.txt", "local source\n")
                self.put(self.remote, "collision.txt", content)
                self.put(self.remote, ".git/info/exclude", "/collision.txt\n")
                session = self.prepare(succeeds=False, error="Remote ignored destination conflict")
                self.assertEqual(self.state(session)["phase"], "failed")
                self.assertFalse((session / "files.nul").exists())
                self.assertEqual((self.remote / "collision.txt").read_text(), content)
                self.assert_run_blocked(session)

    def test_local_content_change_with_same_status_size_and_mtime_invalidates_review(self):
        source = self.put(self.local, "source.txt", "reviewed\n")
        session = self.prepare()
        before = source.stat()
        status = self.git(self.local, "status", "--porcelain=v1", "-z")
        source.write_text("mutated!\n")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(source.stat().st_size, before.st_size)
        self.assertEqual(self.git(self.local, "status", "--porcelain=v1", "-z"), status)
        self.assert_apply_rejected(session, "Local source or index changed")
        self.assertEqual((self.remote / "source.txt").read_text(), "baseline\n")

    def test_remote_content_change_after_preview_invalidates_review(self):
        self.put(self.local, "source.txt", "local replacement\n")
        session = self.prepare()
        self.put(self.remote, "source.txt", "concurrent remote work\n")
        self.assert_apply_rejected(session, "Remote source changed after review")
        self.assertEqual((self.remote / "source.txt").read_text(), "concurrent remote work\n")
        self.assertEqual(self.git(self.remote, "stash", "list"), "")

    def test_changed_local_head_blocks_apply(self):
        session = self.prepare()
        self.git(self.local, "commit", "--allow-empty", "-m", "Changed local HEAD")
        self.assert_apply_rejected(session, "Local source or index changed")
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), self.head)

    def test_changed_remote_head_blocks_apply(self):
        session = self.prepare()
        self.git(self.remote, "commit", "--allow-empty", "-m", "Changed remote HEAD")
        changed = self.git(self.remote, "rev-parse", "HEAD").strip()
        self.assert_apply_rejected(session, "Remote HEAD no longer matches")
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), changed)

    def test_wrong_head_without_shared_reference_stops_without_switching(self):
        self.git(self.remote, "commit", "--allow-empty", "-m", "Unshared remote HEAD")
        changed = self.git(self.remote, "rev-parse", "HEAD").strip()
        session = self.prepare(succeeds=False, error="No matching shared branch")
        self.assertEqual(self.state(session)["phase"], "failed")
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), changed)
        self.assertEqual(self.git(self.remote, "branch", "--show-current").strip(), "master")

    def test_shared_file_remote_aligns_to_exact_local_commit(self):
        upstream = self.home / "upstream.git"
        self.checked([TOOLS["git"], "clone", "--bare", str(self.local), str(upstream)])
        self.git(self.local, "remote", "add", "origin", str(upstream))
        self.git(self.remote, "remote", "set-url", "origin", str(upstream))
        self.put(self.local, "source.txt", "new committed baseline\n")
        self.git(self.local, "add", "source.txt")
        self.git(self.local, "commit", "-m", "Shared baseline")
        self.git(self.local, "push", "-u", "origin", "master")
        target = self.git(self.local, "rev-parse", "HEAD").strip()
        session = self.prepare()
        self.assertEqual(self.state(session)["original"]["info"]["head"], self.head)
        self.assertEqual(self.state(session)["aligned"]["head"], target)
        self.assertEqual(self.state(session)["aligned"]["branch"], "(detached)")
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), target)
        self.assertEqual((self.remote / "source.txt").read_text(), "new committed baseline\n")
        self.apply(session)

    def test_submodule_index_entry_is_rejected_without_remote_access(self):
        self.git(self.local, "update-index", "--add", "--cacheinfo",
                 "160000," + self.head + ",submodule")
        self.prepare(succeeds=False, error="Submodules are not supported")
        self.assertEqual(self.records(self.ssh_log), [])

    def test_submodule_in_baseline_is_rejected_even_if_removed_from_index(self):
        self.git(self.local, "update-index", "--add", "--cacheinfo",
                 "160000," + self.head + ",submodule")
        self.git(self.local, "commit", "-m", "Gitlink fixture")
        self.git(self.local, "rm", "--cached", "submodule")
        self.prepare(succeeds=False, error="Submodules are not supported")
        self.assertEqual(self.records(self.ssh_log), [])

    def test_local_source_parent_symlink_is_rejected(self):
        outside = self.home / "outside-source"
        outside.mkdir()
        self.put(outside, "nested.txt", "outside sentinel\n")
        shutil.rmtree(self.local / "src")
        (self.local / "src").symlink_to(outside, target_is_directory=True)
        self.prepare(succeeds=False, error="Unsafe path parent")
        self.assertEqual(self.records(self.ssh_log), [])
        self.assertEqual((outside / "nested.txt").read_text(), "outside sentinel\n")

    def test_remote_source_parent_symlink_blocks_apply_without_writing_through_it(self):
        session = self.prepare()
        outside = self.remote_home / "outside-source"
        outside.mkdir()
        self.put(outside, "nested.txt", "outside sentinel\n")
        shutil.rmtree(self.remote / "src")
        (self.remote / "src").symlink_to(outside, target_is_directory=True)
        self.assert_apply_rejected(session, "Unsafe path parent")
        self.assertEqual((outside / "nested.txt").read_text(), "outside sentinel\n")
        self.assertTrue((self.remote / "src").is_symlink())

    def test_remote_repository_parent_symlink_is_rejected_before_stash(self):
        parent = self.remote.parent
        moved = self.remote_home / "moved-arkcompiler"
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        actual = moved / "ets_runtime"
        self.put(actual, "source.txt", "must not stash\n")
        self.prepare(succeeds=False, error="Unsafe path parent")
        self.assertEqual(self.git(actual, "stash", "list"), "")
        self.assertEqual((actual / "source.txt").read_text(), "must not stash\n")

    def test_directory_replacing_local_file_is_rejected(self):
        (self.local / "source.txt").unlink()
        self.put(self.local, "source.txt/child", "directory sentinel\n")
        self.prepare(succeeds=False, error="Directories and special files are not supported")
        self.assertEqual(self.records(self.ssh_log), [])
        self.assertEqual((self.remote / "source.txt").read_text(), "baseline\n")

    def test_directory_replacing_remote_file_blocks_apply_without_deleting_children(self):
        session = self.prepare()
        (self.remote / "source.txt").unlink()
        self.put(self.remote, "source.txt/child", "directory sentinel\n")
        self.assert_apply_rejected(session, "Directories and special files are not supported")
        self.assertEqual((self.remote / "source.txt/child").read_text(), "directory sentinel\n")

    def test_changed_reviewed_file_list_blocks_apply(self):
        session = self.prepare()
        with (session / "files.nul").open("ab") as stream:
            stream.write(b"unexpected.txt\0")
        self.assert_apply_rejected(session, "Reviewed file list changed")
        self.assertEqual(self.git(self.remote, "status", "--porcelain"), "")

    def test_run_before_apply_is_blocked(self):
        self.assert_run_blocked(self.prepare())

    def test_source_change_after_apply_blocks_command(self):
        session = self.prepare()
        self.apply(session)
        self.put(self.remote, "source.txt", "changed after apply\n")
        result = self.cli("run", session=session, command="ark build")
        self.assertNotEqual(result.returncode, 0, self.diagnostic(result))
        self.assertIn("Remote source changed after apply", result.stderr)
        self.assertEqual(self.records(self.ark_log), [])
        self.assertEqual(self.state(session)["phase"], "failed")

    def test_ssh_disconnect_retains_lock_and_does_not_retry_command(self):
        session = self.prepare()
        self.apply(session)
        self.env['FAKE_SSH_BREAK_RUN'] = '1'
        result = self.cli('run', session=session, command='ark build')
        self.assertNotEqual(result.returncode, 0, self.diagnostic(result))
        self.assertIn('Remote task state is uncertain; lock retained', result.stderr)
        state = self.state(session)
        self.assertEqual(state['phase'], 'failed')
        self.assertIsNone(state['commands'][0]['exit_code'])
        self.assertTrue(Path(state['remote_lock']).is_dir())
        self.assertEqual(self.records(self.ark_log), [])
        again = self.cli('run', session=session, command='ark build')
        self.assertIn('Run requires a successfully applied session', again.stderr)
        self.assertEqual(len(self.state(session)['commands']), 1)

    def test_remote_lock_blocks_prepare_without_stashing(self):
        self.put(self.remote, 'source.txt', 'concurrent work\n')
        env = dict(self.env, HOME=str(self.remote_home))
        request = {'relative': self.relative.as_posix(), 'token': 'other-owner', 'action': 'lock'}
        locked = self.invoke([sys.executable, '-B', WORKER], data=json.dumps(request), env=env)
        self.assertEqual(locked.returncode, 0, self.diagnostic(locked))
        location = json.loads(locked.stdout)['lock']
        self.prepare(succeeds=False, error='Remote operation locked')
        self.assertEqual((self.remote / 'source.txt').read_text(), 'concurrent work\n')
        self.assertEqual(self.git(self.remote, 'stash', 'list'), '')
        self.assertEqual((Path(location) / 'owner').read_text(), 'other-owner')

    def test_failed_rsync_apply_blocks_build_and_keeps_recovery_lock(self):
        self.put(self.local, 'source.txt', 'new local source\n')
        session = self.prepare()
        self.make_executable('rsync', f'''
            import os
            import sys
            args = sys.argv[1:]
            if any(arg.startswith('--files-from=') for arg in args) and '--dry-run' not in args:
                sys.exit(23)
            os.execv({TOOLS['rsync']!r}, ['rsync', *args])
        ''')
        self.assert_apply_rejected(session, 'Rsync failed (23)')
        self.assertTrue(Path(self.state(session)['remote_lock']).is_dir())
        self.assertEqual((self.remote / 'source.txt').read_text(), 'baseline\n')
        self.assertEqual(self.records(self.ark_log), [])

    def test_stash_failure_stops_before_mirror_without_touching_remote_work(self):
        self.put(self.remote, 'source.txt', 'remote work\n')
        self.make_executable('git', f'''
            import os
            import sys
            if sys.argv[1:3] == ['stash', 'push']:
                sys.stderr.write('Injected stash failure\\n')
                sys.exit(1)
            os.execv({TOOLS['git']!r}, ['git', *sys.argv[1:]])
        ''')
        session = self.prepare(succeeds=False, error='Injected stash failure')
        self.assertEqual((self.remote / 'source.txt').read_text(), 'remote work\n')
        self.assertEqual(self.git(self.remote, 'stash', 'list'), '')
        self.assertFalse((session / 'files.nul').exists())
        self.assert_run_blocked(session)

    def test_stash_rejects_ignored_replacement_of_staged_deleted_file(self):
        self.git(self.remote, 'rm', '--cached', 'source.txt')
        self.put(self.remote, '.git/info/exclude', '/source.txt\n')
        self.put(self.remote, 'source.txt', 'irreplaceable ignored data\n')
        before = self.git(self.remote, 'status', '--porcelain=v1', '-z')
        self.prepare(succeeds=False, error='Remote ignored destination conflict')
        self.assertEqual((self.remote / 'source.txt').read_text(), 'irreplaceable ignored data\n')
        self.assertEqual(self.git(self.remote, 'status', '--porcelain=v1', '-z'), before)
        self.assertEqual(self.git(self.remote, 'stash', 'list'), '')

    def test_stash_rejects_directory_replacement_with_ignored_child(self):
        (self.remote / 'source.txt').unlink()
        self.put(self.remote, 'source.txt/valuable.bin', 'irreplaceable child\n')
        self.put(self.remote, '.git/info/exclude', '/source.txt/\n')
        self.prepare(succeeds=False)
        self.assertEqual((self.remote / 'source.txt/valuable.bin').read_text(), 'irreplaceable child\n')
        self.assertEqual(self.git(self.remote, 'stash', 'list'), '')

    def test_stash_rejects_remote_hidden_index_changes(self):
        for flag, undo in (('--assume-unchanged', '--no-assume-unchanged'),
                           ('--skip-worktree', '--no-skip-worktree')):
            with self.subTest(flag=flag):
                self.git(self.remote, 'update-index', flag, 'source.txt')
                self.put(self.remote, 'source.txt', 'hidden work\n')
                self.put(self.remote, 'mode.sh', 'visible work\n')
                self.prepare(succeeds=False, error='Sparse checkout and assume-unchanged')
                self.assertEqual((self.remote / 'source.txt').read_text(), 'hidden work\n')
                self.assertEqual((self.remote / 'mode.sh').read_text(), 'visible work\n')
                self.assertEqual(self.git(self.remote, 'stash', 'list'), '')
                self.git(self.remote, 'update-index', undo, 'source.txt')

    def assert_hidden_content_is_protected(self, visible_work):
        self.git(self.remote, 'config', 'core.trustctime', 'false')
        source = self.remote / 'source.txt'
        os.utime(source, ns=(1600000000000000000, 1600000000000000000))
        self.git(self.remote, 'update-index', '--refresh')
        before = source.stat()
        source.write_text('precious\n')
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        if visible_work:
            self.put(self.remote, 'mode.sh', 'visible work\n')
        self.assertNotIn('source.txt', self.git(self.remote, 'status', '--porcelain'))
        self.assertEqual(self.git(self.remote, 'ls-files', '-v', 'source.txt'), 'H source.txt\n')
        self.prepare(succeeds=False, error='Git did not report changed tracked content')
        self.assertEqual(source.read_text(), 'precious\n')
        self.assertEqual(self.git(self.remote, 'stash', 'list'), '')
        if visible_work:
            self.assertEqual((self.remote / 'mode.sh').read_text(), 'visible work\n')

    def test_stat_cache_hidden_modification_blocks_stash(self):
        self.assert_hidden_content_is_protected(visible_work=True)

    def test_stat_cache_hidden_modification_blocks_apparently_clean_remote(self):
        self.assert_hidden_content_is_protected(visible_work=False)

    def test_content_guard_handles_git_eol_filters_and_quoted_paths(self):
        self.put(self.local, '.gitattributes', 'source.txt text eol=crlf\n')
        name = 'quote"back\\slash-中文\nfile.txt'
        self.put(self.local, name, 'quoted path\n')
        self.git(self.local, 'add', '.gitattributes', name)
        self.git(self.local, 'commit', '-m', 'Attributes and special filename')
        target = self.git(self.local, 'rev-parse', 'HEAD').strip()
        self.git(self.remote, 'fetch', 'origin')
        self.git(self.remote, 'switch', '--detach', target)
        (self.remote / 'source.txt').write_bytes(b'baseline\r\n')
        self.git(self.remote, 'add', '--', 'source.txt')
        self.assertEqual(self.git(self.remote, 'status', '--porcelain'), '')
        session = self.prepare()
        self.apply(session)
        self.assertEqual((self.remote / name).read_text(), 'quoted path\n')
        self.assertEqual((self.remote / 'source.txt').read_bytes(), b'baseline\n')

    def test_forced_indexed_ignored_source_can_apply_and_run(self):
        self.put(self.local, 'out/forced.cc', 'force-added source\n')
        self.git(self.local, 'add', '-f', 'out/forced.cc')
        self.put(self.remote, 'out/existing.bin', 'keep artifact\n')
        session = self.prepare()
        self.apply(session)
        result = self.cli('run', session=session, command='ark build')
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        self.assertEqual((self.remote / 'out/forced.cc').read_text(), 'force-added source\n')
        self.assertEqual((self.remote / 'out/existing.bin').read_text(), 'keep artifact\n')

    def test_command_failure_returns_exact_exit_code_without_sync_or_retry(self):
        session = self.prepare()
        self.apply(session)
        result = self.cli("run", session=session, command="ark build fail")
        self.assertEqual(result.returncode, 23, self.diagnostic(result))
        self.assertIn("Fake ark: build fail", (session / "remote.log").read_text())
        state = self.state(session)
        self.assertEqual(state["phase"], "applied")
        self.assertEqual(state["ark_sync"], "skipped")
        self.assertEqual(len(state["commands"]), 1)
        self.assertEqual(state["commands"][0]["command"], "ark build fail")
        self.assertEqual(state["commands"][0]["exit_code"], 23)
        self.assertEqual(self.records(self.ark_log), [
            {"args": ["build", "fail"], "cwd": str(self.remote), "home": str(self.remote_home)}])
        self.assertFalse(Path(state["remote_lock"]).exists())
        self.assertEqual(self.git(self.remote, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.git(self.remote, "stash", "list"), "")
        # An ordinary command failure does not poison an otherwise valid mirror.
        result = self.cli("run", session=session, command="ark build")
        self.assertEqual(result.returncode, 0, self.diagnostic(result))
        self.assertEqual([record["args"] for record in self.records(self.ark_log)],
                         [["build", "fail"], ["build"]])


if __name__ == "__main__":
    unittest.main()
