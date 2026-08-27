#!/usr/bin/env bash
# get-d8.sh — download / update the prebuilt V8 `d8` shell from the public GCS bucket
#   https://storage.googleapis.com/chromium-v8/official/<branch>/v8-<platform>-rel-<fullversion>.zip
#
# Installs the real binary + data files under ~/.local/share/v8-d8/<fullversion>/ and
# exposes it as ~/.local/bin/d8 via a *wrapper script* (not a symlink), so d8's data
# files (icudtl.dat / snapshot_blob.bin) resolve next to the real binary.
#
# GCS (storage.googleapis.com) is reachable directly on most networks — including
# China, where chromium.googlesource.com is often blocked. No proxy needed.
# Throughput can be modest; downloads resume via `curl -C -`.
#
# Usage:
#   get-d8.sh                 # install/update to latest linux64 release
#   get-d8.sh linux64         # explicit platform
#   get-d8.sh linux64 15.3    # pin to branch 15.3
#
# Platforms (form the file prefix v8-<platform>-rel):
#   linux64 linux32 linux-arm64 linux-arm32
#   mac-arm64 mac-x64
#   android-arm64 android-arm32
# For debug builds / the -libs-rel variant, edit FILE_PREFIX below.
set -euo pipefail

PLATFORM="${1:-linux64}"
PIN_BRANCH="${2:-}"
BUCKET="https://storage.googleapis.com/chromium-v8"
SHARE="${D8_HOME:-$HOME/.local/share/v8-d8}"
BIN_DIR="${D8_BIN_DIR:-$HOME/.local/bin}"
FILE_PREFIX="v8-${PLATFORM}-rel"

need(){ command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 1; }; }
need curl; need unzip; need python3; need grep; need sed; need sort

# List branch prefixes official/<X.Y>/
list_branches(){
  curl -sS --max-time 30 "$BUCKET/?prefix=official/&delimiter=/&max-keys=1000" \
    | grep -oE '<Prefix>official/[0-9]+\.[0-9]+/</Prefix>' \
    | sed -E 's#<Prefix>official/([0-9.]+)/</Prefix>#\1#' | sort -V -u
}

# Newest full-version rel zip for a branch (empty if the branch has none for this platform)
latest_file_in_branch(){
  local b="$1"
  curl -sS --max-time 30 "$BUCKET/?prefix=official/$b/$FILE_PREFIX&max-keys=50" \
    | grep -oE "<Key>official/$b/$FILE_PREFIX-[^<]+\.zip</Key>" \
    | sed -E 's#</?Key>##g' | sort -V | tail -1
}

echo ">> discovering latest $FILE_PREFIX build …"
LATEST=""
if [ -n "$PIN_BRANCH" ]; then
  LATEST="$(latest_file_in_branch "$PIN_BRANCH" || true)"
else
  # newest branch first; stop at the first branch that actually carries a file for this platform
  while read -r b; do
    f="$(latest_file_in_branch "$b" || true)"
    if [ -n "$f" ]; then LATEST="$f"; break; fi
  done < <(list_branches | sort -V -r)
fi
[ -n "$LATEST" ] || { echo "no $FILE_PREFIX build found (pin=$PIN_BRANCH)" >&2; exit 1; }

FULL="$(printf '%s' "$LATEST" | sed -E "s#.*/$FILE_PREFIX-([0-9.]+)\.zip#\1#")"
URL="$BUCKET/$LATEST"
echo ">> latest: $FULL   ($URL)"

# Already at this version?
INSTALLED="$(readlink "$SHARE/current" 2>/dev/null || true)"
if [ "$INSTALLED" = "$FULL" ] && [ -x "$SHARE/$FULL/d8" ]; then
  echo ">> already at $FULL — up to date."
else
  DEST="$SHARE/$FULL"
  TMP="$(mktemp)"
  echo ">> downloading (resumable, may be slow) …"
  curl -fSL --retry 5 --retry-delay 3 -C - -o "$TMP" "$URL"
  mkdir -p "$DEST"
  unzip -o -q "$TMP" -d "$DEST"
  rm -f "$TMP"
  chmod +x "$DEST/d8"
  ln -sfn "$FULL" "$SHARE/current"        # active version
  # keep only the latest two version dirs (free disk)
  ls -1d "$SHARE"/*/ 2>/dev/null | sort -r | tail -n +3 | xargs -r rm -rf
  echo ">> installed $FULL"
fi

# (re)write the ~/.local/bin/d8 wrapper — a real file, never a symlink (see skill notes).
# A bare symlink breaks ICU init because d8 resolves data files via argv[0]'s dir.
install -d "$BIN_DIR"
rm -f "$BIN_DIR/d8"
cat > "$BIN_DIR/d8" <<'EOF'
#!/usr/bin/env bash
# d8 wrapper: runs the active V8 d8 build. Data files (icudtl.dat / snapshot_blob.bin)
# resolve correctly because argv[0] points at the real binary inside the version dir.
set -eu
REAL="$HOME/.local/share/v8-d8/current/d8"
if [ ! -x "$REAL" ]; then
  echo "d8 not installed under ~/.local/share/v8-d8/. Run the get-d8 script/skill first." >&2
  exit 1
fi
exec "$REAL" "$@"
EOF
chmod +x "$BIN_DIR/d8"

echo ">> done.  $("$SHARE/current/d8" --version 2>/dev/null | head -1)"
echo "   run:  d8 --version   (ensure $BIN_DIR is on your PATH)"
