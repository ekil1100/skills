---
name: d8-binary
description: Download, install, or update the prebuilt V8 `d8` developer shell from the official public GCS bucket (gs://chromium-v8/official). Use whenever the user wants to get/install/update/upgrade d8, run V8's standalone shell, obtain a prebuilt V8 binary, or says things like "下载 d8 / 更新 d8 / 装 d8 / 跑 d8 / 最新 d8 / upgrade d8". Trigger even when the user doesn't say "GCS" or "prebuilt" — if they want a ready-to-run d8 / V8 shell without compiling from source, this is the skill. Also trigger when a build from source is blocked (e.g. missing `chromium/src/build` deps, googlesource unreachable, no proxy) and the user just needs to run V8.
---

# d8 binary — download & update

`d8` is V8's standalone developer shell. Official prebuilt builds live in a **public** Google Cloud Storage bucket and are fetched with plain `curl` — **no proxy needed**, even on networks where `chromium.googlesource.com` is blocked. GCS (`storage.googleapis.com`) sits on a different, independently reachable CDN; throughput can be modest (~tens of KB/s to MB/s) so downloads resume with `curl -C -`.

This is the path to take when building V8 from source isn't feasible (missing chromium deps, googlesource blocked, no working proxy) — the user still gets a runnable current `d8`.

## Layout

- Real binary + data files → `~/.local/share/v8-d8/<fullversion>/` — `d8`, `icudtl.dat`, `snapshot_blob.bin`, `v8_build_config.json` must all stay together (d8 finds them via its own `argv[0]` path).
- `~/.local/share/v8-d8/current` → symlink to the active version.
- `~/.local/bin/d8` → a **wrapper script** (NOT a symlink), so `argv[0]` resolves to the real binary and d8 can locate its data files.

## Install / update

Run the bundled script (idempotent — re-running updates to the latest and repoints `current`):

```bash
bash ~/.pi/agent/skills/d8-binary/scripts/get-d8.sh              # latest linux64 release
bash ~/.pi/agent/skills/d8-binary/scripts/get-d8.sh linux64      # explicit platform
bash ~/.pi/agent/skills/d8-binary/scripts/get-d8.sh linux64 15.3 # pin to branch 15.3
```

Then make sure `~/.local/bin` is on `PATH` and run `d8 --version`.

The script scans the bucket's branches newest-first and picks the newest one that actually carries a `v8-<platform>-rel` zip — because not every `official/<X.Y>/` directory has a build for every platform (newer branch dirs are often empty placeholders).

## Platforms

Pass a platform token (forms the file prefix `v8-<platform>-rel`):

- `linux64` (default), `linux32`, `linux-arm64`, `linux-arm32`
- `mac-arm64`, `mac-x64`
- `android-arm64`, `android-arm32`

For debug builds or the `-libs-rel` variant, set `FILE_PREFIX` in the script (e.g. `v8-linux64-dbg`).

## Source URL

`https://storage.googleapis.com/chromium-v8/official/<branch>/v8-<platform>-rel-<fullversion>.zip`

The zip expands to `d8` + `icudtl.dat` + `snapshot_blob.bin` + `v8_build_config.json`. All four are needed to run; keep them in the version dir.

Discover available branches yourself:
```bash
curl "https://storage.googleapis.com/chromium-v8/?prefix=official/&delimiter=/&max-keys=1000" | grep -oE 'official/[0-9.]+/'
```

## Why a wrapper, not a symlink

d8 locates `icudtl.dat` / `snapshot_blob.bin` by the directory of **`argv[0]` as invoked**. A `~/.local/bin/d8` symlink to the real binary makes `argv[0]` = `~/.local/bin/d8`, so d8 looks in `~/.local/bin` (no data files there) → `Failed to initialize ICU`. The wrapper does `exec "$HOME/.local/share/v8-d8/current/d8" "$@"`, which makes `argv[0]` point inside the version dir where the data files live. This is why the script always writes a wrapper file rather than a symlink — and why you must `rm -f` any pre-existing symlink at that path before writing (otherwise `cat >` writes *through* the symlink and corrupts the real binary).

## Running

```bash
d8 --version
d8 -e 'print([1,2,3].map(x=>x*x).join(","))'   # clean one-liner
d8 --module script.mjs                         # module mode (top-level await etc.)
echo 'print("hi")' | d8                        # via stdin (also prints the banner)
d8 --help                                      # e.g. --expose-gc, --jitless, --stack-trace, --allow-natives-syntax
```
