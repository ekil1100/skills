"""Run with: python3 -m unittest discover -s wiki-save/scripts -v.

Node executes the real eval payload against an in-memory Obsidian API fixture.
No test invokes the installed CLI or writes to a real vault.
"""

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import wiki_save as subject


HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const {code, fixture} = JSON.parse(fs.readFileSync(0, 'utf8'));
let text = fixture.text;
let writes = 0;
// Full Nl/Dl/Bl pipeline from Obsidian 1.13.7 app.js; see references/cli.md.
const normalizePath = path => {
  path = path.replace(/([\\/])+/g, '/').replace(/(^\/+|\/+$)/g, '');
  if (path === '') path = '/';
  return path.replace(/\u00A0|\u202F/g, ' ').normalize('NFC');
};
const file = {path: normalizePath(fixture.path)};
const rawRead = async path => {
  if (text === null || path !== file.path) throw Error('Note does not exist');
  return fixture.badReadback && writes ? 'changed after write' : text;
};
// Model Obsidian 1.13.7: Vault.read strips one BOM, adapter reads and process
// callbacks retain it, and Vault.create normalizes paths. See references/cli.md.
const vault = {
  getName: () => fixture.wrongVault ? 'other' : 'lib',
  adapter: {getBasePath: () => '/vault/lib', read: rawRead},
  getFileByPath: path => text === null || path !== file.path ? null : file,
  getAbstractFileByPath: path => text === null || path !== file.path ? null : file,
  read: async file => (await rawRead(file.path)).replace(/^\uFEFF/, ''),
  create: async (path, content) => {
    if (text !== null || fixture.createRace) throw Error('Already exists');
    file.path = normalizePath(path);
    text = content; writes++; return file;
  },
  process: async (file, callback) => {
    text = callback(fixture.race ? (fixture.concurrentText ?? 'concurrent edit') : text);
    writes++;
  }
};
if (fixture.noProcess) delete vault.process;
(async () => {
  const result = await vm.runInNewContext(code, {
    app: {vault}, TextDecoder, TextEncoder, atob, btoa
  });
  console.log(JSON.stringify({result, text, writes, path: file.path}));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


class NormalizerTests(unittest.TestCase):
    def test_native_create_normalization_model(self):
        # Exercise the fixture directly, bypassing guards, so it must reproduce
        # the wrong-path write that the actual payload must prevent.
        cases = (
            ("wiki/A\u00a0B.md", "wiki/A B.md"),
            ("A\u202fB/Test.md", "A B/Test.md"),
            ("wiki/Cafe\u0301.md", "wiki/Caf\u00e9.md"),
            ("/wiki//Test.md/", "wiki/Test.md"),
            ("\\wiki\\\\Test.md\\", "wiki/Test.md"),
            ("", "/"), ("///", "/"),
            ("//A\u00a0B\\Cafe\u0301\u202fX.md/", "A B/Caf\u00e9 X.md"),
        )
        for path, normalized in cases:
            with self.subTest(path=path):
                code = f"app.vault.create({json.dumps(path)}, 'new')"
                result = subprocess.run(["node", "-e", HARNESS],
                                        input=json.dumps({"code": code, "fixture": {"path": path, "text": None}}),
                                        capture_output=True, text=True, check=True)
                state = json.loads(result.stdout)
                self.assertEqual(state["path"], normalized)
                self.assertEqual(state["text"], "new")
                self.assertEqual(state["writes"], 1)


class VaultTests(unittest.TestCase):
    def resolve(self, listing, expected_path="/vault/lib", explicit=None):
        with patch.object(subject, "cli", side_effect=[listing, expected_path]) as cli:
            result = subject.resolve_vault(explicit)
        return result, cli.call_args_list

    def test_priority_not_listing_order(self):
        result, calls = self.resolve("library\t/vault/library\nwiki\t/vault/wiki\nlib\t/vault/lib")
        self.assertEqual(result["vault"], "lib")
        self.assertEqual(calls[1].args, ("vault=lib", "vault", "info=path"))

    def test_fallback_order(self):
        for names, chosen in ((["library", "wiki"], "wiki"), (["library"], "library")):
            with self.subTest(chosen=chosen):
                result, _ = self.resolve("\n".join(f"{n}\t/vault/{n}" for n in names), f"/vault/{chosen}")
                self.assertEqual(result["vault"], chosen)

    def test_explicit_precedence(self):
        result, _ = self.resolve("lib\t/vault/lib\ntest\t/vault/test", "/vault/test", "test")
        self.assertEqual(result["vault"], "test")

    def test_unrelated_duplicate_names_do_not_block_selection(self):
        listing = "notes\t/vault/notes-a\nlib\t/vault/lib\nnotes\t/vault/notes-b"
        for explicit in (None, "lib"):
            with self.subTest(explicit=explicit):
                result, calls = self.resolve(listing, explicit=explicit)
                self.assertEqual(result, {"vault": "lib", "vault_path": "/vault/lib"})
                self.assertEqual(calls[1].args, ("vault=lib", "vault", "info=path"))
        # An explicit unique name also takes precedence over an ambiguous default.
        result, _ = self.resolve("lib\t/vault/a\nlib\t/vault/b\ntest\t/vault/test",
                                 "/vault/test", "test")
        self.assertEqual(result["vault"], "test")

    def test_selected_duplicate_name_stops_without_fallback(self):
        for explicit, name in ((None, "lib"), (None, "wiki"), ("notes", "notes")):
            listing = f"{name}\t/vault/a\n{name}\t/vault/b\nlibrary\t/vault/library"
            with self.subTest(explicit=explicit, name=name), \
                    patch.object(subject, "cli", return_value=listing) as cli:
                with self.assertRaisesRegex(subject.SaveError, "Ambiguous registered vault name"):
                    subject.resolve_vault(explicit)
                cli.assert_called_once_with("vaults", "verbose")

    def test_missing_and_explicit_typo_stop(self):
        for explicit in (None, "typo"):
            with patch.object(subject, "cli", return_value="other\t/vault/other"):
                with self.assertRaises(subject.SaveError):
                    subject.resolve_vault(explicit)

    def test_path_mismatch_stops(self):
        with self.assertRaises(subject.SaveError):
            self.resolve("lib\t/vault/lib\nwiki\t/vault/wiki", "/vault/other")

    def test_invalid_paths(self):
        for path in ("/x.md", "../x.md", "wiki/../x.md", ".obsidian/x.md", "wiki//x.md", "x\\y.md", "x.md\n", "x.json"):
            with self.subTest(path=path), self.assertRaises(subject.SaveError):
                subject.validate_path(path)

    def test_nfc_paths(self):
        for path in ("wiki/Caf\u00e9.md", "wiki/中文.md"):
            self.assertEqual(subject.validate_path(path), path)
        for path in ("wiki/Cafe\u0301.md", "Cafe\u0301/Test.md"):
            with self.subTest(path=path), self.assertRaisesRegex(subject.SaveError, "NFC"):
                subject.validate_path(path)

    def test_non_nfc_path_stops_before_evaluation(self):
        path = "wiki/Cafe\u0301.md"
        target = {"vault": "lib", "vault_path": "/vault/lib"}
        snapshot = {"version": 1, **target, "path": path, "content": "old"}
        for command in ("create", "snapshot", "update"):
            args = SimpleNamespace(command=command, vault=None, path=path,
                                   snapshot="snapshot.json", content="candidate.md", out="out.json")
            with self.subTest(command=command), \
                    patch.object(subject, "resolve_vault", return_value=target), \
                    patch.object(subject, "read_utf8", return_value=json.dumps(snapshot)), \
                    patch.object(subject, "evaluate") as evaluate, \
                    patch.object(subject, "save_snapshot") as save_snapshot:
                with self.assertRaisesRegex(subject.SaveError, "NFC"):
                    subject.run(args)
                evaluate.assert_not_called()
                save_snapshot.assert_not_called()

    def test_nonbreaking_spaces_stop_before_evaluation(self):
        target = {"vault": "lib", "vault_path": "/vault/lib"}
        for char in ("\u00a0", "\u202f"):
            for path in (f"wiki/A{char}B.md", f"A{char}B/Test.md"):
                self.assertEqual(subject.unicodedata.normalize("NFC", path), path)
                with self.subTest(path=path), self.assertRaisesRegex(subject.SaveError, "U\\+00A0.*U\\+202F"):
                    subject.validate_path(path)
                snapshot = {"version": 1, **target, "path": path, "content": "old"}
                for command in ("create", "snapshot", "update"):
                    args = SimpleNamespace(command=command, vault=None, path=path,
                                           snapshot="snapshot.json", content="candidate.md", out="out.json")
                    with self.subTest(path=path, command=command), \
                            patch.object(subject, "resolve_vault", return_value=target), \
                            patch.object(subject, "read_utf8", return_value=json.dumps(snapshot)), \
                            patch.object(subject, "evaluate") as evaluate, \
                            patch.object(subject, "save_snapshot") as save_snapshot:
                        with self.assertRaisesRegex(subject.SaveError, "U\\+00A0.*U\\+202F"):
                            subject.run(args)
                        evaluate.assert_not_called()
                        save_snapshot.assert_not_called()

    def test_cli_uses_argument_array(self):
        result = subprocess.CompletedProcess([], 0, "ok", "")
        with patch.object(subject.subprocess, "run", return_value=result) as run:
            self.assertEqual(subject.cli("vault=lib", "eval", "code=$(danger)"), "ok")
        self.assertEqual(run.call_args.args[0], ["obsidian", "vault=lib", "eval", "code=$(danger)"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_missing_result_is_failure_even_with_zero_exit(self):
        with patch.object(subject, "cli", return_value="Error: eval unavailable"):
            with self.assertRaises(subject.SaveError):
                subject.evaluate({"vault": "lib"})

    def test_update_pins_snapshot_vault_and_path(self):
        snapshot = {"version": 1, "vault": "library", "vault_path": "/vault/library",
                    "path": "wiki/Test.md", "content": "old"}
        args = SimpleNamespace(command="update", vault=None, snapshot="snapshot.json", content="candidate.md")
        with patch.object(subject, "read_utf8", side_effect=[json.dumps(snapshot), "new"]), \
                patch.object(subject, "resolve_vault", return_value={"vault": "library", "vault_path": "/vault/library"}) as resolve, \
                patch.object(subject, "evaluate", return_value={"ok": True, "verified": True, "path": "wiki/Test.md"}) as evaluate:
            subject.run(args)
        resolve.assert_called_once_with("library")
        self.assertEqual(evaluate.call_args.args[0]["expected"], "old")
        self.assertEqual(evaluate.call_args.args[0]["path"], "wiki/Test.md")
        args.vault = "lib"
        with patch.object(subject, "read_utf8", return_value=json.dumps(snapshot)), \
                patch.object(subject, "evaluate") as evaluate:
            with self.assertRaises(subject.SaveError):
                subject.run(args)
        evaluate.assert_not_called()

    def test_snapshot_exclusive_and_exact_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            value = {"content": "\ufeff中文\r\n`\\n`\n"}
            subject.save_snapshot(path, value)
            self.assertEqual(json.loads(subject.read_utf8(path)), value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                subject.save_snapshot(path, {})
            raw = Path(folder) / "raw.md"
            raw.write_bytes(b"\xef\xbb\xbfa\r\nb\n")
            self.assertEqual(subject.read_utf8(raw), "\ufeffa\r\nb\n")


class PayloadTests(unittest.TestCase):
    def execute(self, action="update", text="old", content="new", expected="old",
                path="wiki/Test.md", **fixture):
        request = {"vault": "lib", "vault_path": "/vault/lib", "path": path,
                   "action": action, "expected": expected, "content": content}
        state = {}

        def fake_cli(*args):
            code = args[-1].removeprefix("code=")
            result = subprocess.run(["node", "-e", HARNESS],
                                    input=json.dumps({"code": code, "fixture": {"text": text, "path": path, **fixture}}),
                                    capture_output=True, text=True, check=True)
            state.update(json.loads(result.stdout))
            return "Startup notice\n=> " + state["result"] + "\n"

        with patch.object(subject, "cli", side_effect=fake_cli):
            try:
                result = subject.evaluate(request)
            except subject.SaveError as error:
                result = str(error)
        return result, state

    def test_snapshot(self):
        result, state = self.execute(action="snapshot")
        self.assertEqual(result["content"], "old")
        self.assertEqual(state["writes"], 0)

    def test_create_and_update_roundtrip(self):
        content = '---\ntags: [test]\n---\n中文 😀\r\n```js\nconst s = "\\n";\n```\n$(echo SHOULD_NOT_RUN) `x` ${x} \' \\t\n'
        for action, text in (("create", None), ("update", "old")):
            with self.subTest(action=action):
                result, state = self.execute(action=action, text=text, content=content)
                self.assertTrue(result["verified"])
                self.assertEqual(state["text"], content)
                self.assertEqual(state["writes"], 1)

    def test_bom_create_roundtrip(self):
        content = '\ufeff---\r\ntags: [test]\r\n---\r\n中文 Cafe\u0301 `\\n`\r\n'
        result, state = self.execute(action="create", text=None, content=content)
        self.assertEqual(state["writes"], 1)
        self.assertEqual(state["text"], content)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["verified"])

    def test_bom_snapshot_to_update(self):
        original = '\ufeff中文 Cafe\u0301 `\\n`\r\n'
        snapshot, state = self.execute(action="snapshot", text=original)
        self.assertEqual(state["writes"], 0)
        self.assertEqual(snapshot["content"], original)
        for content in (original, original + "new\r\n", original[1:]):
            with self.subTest(content=content):
                result, state = self.execute(text=original, expected=snapshot["content"],
                                             content=content)
                self.assertIsInstance(result, dict)
                self.assertTrue(result["verified"])
                self.assertEqual(state["text"], content)
                self.assertEqual(state["writes"], int(content != original))

    def test_bom_only_snapshot_conflict(self):
        # A BOM-only concurrent change must conflict, even for a no-op candidate.
        for original, current in (("old", "\ufeffold"), ("\ufeffold", "old")):
            for content in (original, "new"):
                with self.subTest(original=original, content=content):
                    result, state = self.execute(text=current, expected=original, content=content)
                    self.assertEqual(state["writes"], 0)
                    self.assertEqual(state["text"], current)
                    self.assertIn("Snapshot conflict; written=False", result)
        result, state = self.execute(text="\ufeffold", expected="\ufeffold",
                                     race=True, concurrentText="old")
        self.assertEqual(state["writes"], 0)
        self.assertIn("Snapshot conflict; written=False", result)

    def test_non_nfc_path_rejected_before_mutation(self):
        for path in ("wiki/Cafe\u0301.md", "Cafe\u0301/Test.md"):
            for action, text in (("create", None), ("snapshot", "old"), ("update", "old")):
                with self.subTest(path=path, action=action):
                    result, state = self.execute(action=action, text=text, path=path)
                    self.assertEqual(state["writes"], 0)
                    self.assertEqual(state["text"], text)
                    self.assertIn("NFC", result)
                    self.assertIn("written=False", result)

    def test_nonbreaking_spaces_rejected_before_mutation(self):
        for char in ("\u00a0", "\u202f"):
            for path in (f"wiki/A{char}B.md", f"A{char}B/Test.md"):
                for action, text in (("create", None), ("snapshot", "old"), ("update", "old")):
                    with self.subTest(path=path, action=action):
                        result, state = self.execute(action=action, text=text, path=path)
                        self.assertEqual(state["writes"], 0)
                        self.assertEqual(state["text"], text)
                        self.assertIn("U+00A0", result)
                        self.assertIn("U+202F", result)
                        self.assertIn("written=False", result)

    def test_separator_transforms_rejected_before_mutation(self):
        for path in ("/wiki/Test.md", "wiki//Test.md", "wiki\\Test.md",
                     "wiki/Test.md/", "", "/", "///"):
            with self.subTest(path=path):
                with self.assertRaises(subject.SaveError):
                    subject.validate_path(path)
                result, state = self.execute(action="create", text=None, path=path)
                self.assertEqual(state["writes"], 0)
                self.assertIsNone(state["text"])
                self.assertIn("vault-relative Markdown path", result)
                self.assertIn("written=False", result)

    def test_nfc_create_preserves_exact_path(self):
        # Dl replaces only its two named spaces, not all Unicode whitespace.
        for path in ("wiki/Caf\u00e9.md", "wiki/中文.md", " wiki / A B .md",
                     "wiki/A\u2007B\u2009C\u3000D\ufeff.md"):
            with self.subTest(path=path):
                self.assertEqual(subject.validate_path(path), path)
                result, state = self.execute(action="create", text=None, path=path)
                self.assertTrue(result["verified"])
                self.assertEqual(result["path"], path)
                self.assertEqual(state["path"], path)
                self.assertEqual(state["writes"], 1)

    def test_conflict_and_unavailable_process(self):
        for fixture in ({"race": True}, {"noProcess": True}, {"wrongVault": True}):
            with self.subTest(fixture=fixture):
                result, state = self.execute(**fixture)
                self.assertIsInstance(result, str)
                self.assertEqual(state["writes"], 0)

    def test_create_collision_including_race(self):
        for text, fixture in (("existing", {}), (None, {"createRace": True})):
            result, state = self.execute(action="create", text=text, **fixture)
            self.assertIsInstance(result, str)
            self.assertEqual(state["writes"], 0)

    def test_readback_failure_reports_write(self):
        result, state = self.execute(badReadback=True)
        self.assertIn("Readback mismatch; written=True", result)
        self.assertEqual(state["writes"], 1)

    def test_unchanged_content(self):
        result, state = self.execute(content="old")
        self.assertFalse(result["written"])
        self.assertTrue(result["verified"])
        self.assertEqual(state["writes"], 0)


if __name__ == "__main__":
    unittest.main()
