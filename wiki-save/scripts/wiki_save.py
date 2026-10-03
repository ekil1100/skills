#!/usr/bin/env python3
"""Resolve a vault and publish exact Markdown through the Obsidian CLI."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import unicodedata


DEFAULT_VAULTS = ("lib", "wiki", "library")
MARKER = "WIKI_SAVE_RESULT:"
TEMPLATE = Path(__file__).with_name("vault_io.js")


class SaveError(Exception):
    pass


def cli(*args):
    try:
        result = subprocess.run(
            ["obsidian", *args], capture_output=True, text=True, check=False,
        )
    except OSError as error:
        raise SaveError("Obsidian CLI unavailable; start Obsidian and check PATH") from error
    if result.returncode:
        # Avoid echoing commands or payloads that may contain private note text.
        raise SaveError("Obsidian CLI failed; check the app and CLI availability")
    return result.stdout


def resolve_vault(explicit=None):
    rows = {}
    for line in cli("vaults", "verbose").splitlines():
        name, separator, path = line.partition("\t")
        if separator and os.path.isabs(path):
            rows.setdefault(name, []).append(path)
    choices = (explicit,) if explicit is not None else DEFAULT_VAULTS
    for name in choices:
        if name in rows:
            if len(rows[name]) != 1:
                raise SaveError("Ambiguous registered vault name")
            path = rows[name][0]
            actual = cli(f"vault={name}", "vault", "info=path")
            if path not in actual.splitlines():
                raise SaveError("Vault path verification failed; no fallback was attempted")
            return {"vault": name, "vault_path": path}
    raise SaveError("No matching registered vault; specify --vault with an exact name")


def validate_path(path):
    if (not isinstance(path, str) or not path.endswith(".md") or "\\" in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(not part or part.startswith(".") for part in path.split("/"))):
        raise SaveError("Expected a visible, vault-relative Markdown path")
    # Obsidian Nl also replaces these two spaces before NFC normalization.
    if "\u00a0" in path or "\u202f" in path:
        raise SaveError("Path contains U+00A0 or U+202F; Obsidian replaces them with U+0020. "
                        "Confirm and supply the exact space-normalized path before saving")
    if unicodedata.normalize("NFC", path) != path:
        raise SaveError("Expected an NFC-normalized path; supply the exact NFC path before saving")
    return path


def evaluate(request):
    payload = base64.b64encode(json.dumps(request, ensure_ascii=False).encode()).decode()
    code = TEMPLATE.read_text(encoding="utf-8").replace("__PAYLOAD__", payload)
    output = cli(f"vault={request['vault']}", "eval", f"code={code}")
    results = []
    for line in output.splitlines():
        line = line.removeprefix("=> ").strip()
        if line.startswith(MARKER):
            try:
                results.append(json.loads(base64.b64decode(
                    line[len(MARKER):], validate=True).decode("utf-8")))
            except (ValueError, UnicodeError) as error:
                raise SaveError("Invalid CLI result; inspect the note before retrying") from error
    if len(results) != 1 or not isinstance(results[0], dict):
        raise SaveError("Missing CLI result; inspect the note before retrying")
    result = results[0]
    if result.get("ok") is not True:
        detail = result.get("error", "Unknown error")
        raise SaveError(f"{detail}; written={result.get('written', 'unknown')}. "
                        "Keep the snapshot and inspect the note before retrying")
    return result


def read_utf8(path):
    # Preserve CRLF, trailing newlines, and literal backslashes.
    return Path(path).read_bytes().decode("utf-8")


def save_snapshot(path, value):
    # Exclusive creation preserves an earlier recovery snapshot.
    with open(path, "x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run(args):
    if args.command == "resolve":
        return resolve_vault(args.vault)
    if args.command == "update":
        snapshot = json.loads(read_utf8(args.snapshot))
        required = ("vault", "vault_path", "path", "content")
        if (not isinstance(snapshot, dict) or snapshot.get("version") != 1
                or any(not isinstance(snapshot.get(key), str) for key in required)):
            raise SaveError("Invalid snapshot")
        if args.vault is not None and args.vault != snapshot["vault"]:
            raise SaveError("Explicit vault differs from snapshot")
        target = resolve_vault(snapshot["vault"])
        if target["vault_path"] != snapshot["vault_path"]:
            raise SaveError("Vault moved since snapshot; read a new snapshot")
        request = {**target, "action": "update", "path": validate_path(snapshot["path"]),
                   "expected": snapshot["content"], "content": read_utf8(args.content)}
    else:
        path = validate_path(args.path)
        target = resolve_vault(args.vault)
        request = {**target, "action": args.command, "path": path}
        if args.command == "create":
            request["content"] = read_utf8(args.content)
    result = evaluate(request)
    if args.command == "snapshot":
        if not isinstance(result.get("content"), str):
            raise SaveError("Snapshot content missing")
        save_snapshot(args.out, {"version": 1, **target,
                                 "path": request["path"], "content": result["content"]})
        return {**target, "path": request["path"], "snapshot": args.out}
    if result.get("verified") is not True or result.get("path") != request["path"]:
        raise SaveError("Write verification missing; inspect the note before retrying")
    return {**target, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "snapshot", "create", "update"):
        command = commands.add_parser(name)
        command.add_argument("--vault", help="Exact registered name; default: lib, wiki, library")
        if name in ("snapshot", "create"):
            command.add_argument("--path", required=True, help="Vault-relative Markdown path")
        if name == "snapshot":
            command.add_argument("--out", required=True, help="New local recovery snapshot JSON")
        if name == "update":
            command.add_argument("--snapshot", required=True, help="Snapshot pins vault and note path")
        if name in ("create", "update"):
            command.add_argument("--content", required=True, help="Local UTF-8 candidate file")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (SaveError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
