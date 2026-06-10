---
name: update-claude
description: Update Claude Code CLI by downloading the native binary directly from npm registry. Use this when the user wants to update Claude Code, says "claude update is slow", "update claude", "升级 claude", "更新 claude", or when `claude update` fails/hangs. This bypasses the slow built-in updater by pulling from registry.npmjs.org (Cloudflare CDN) instead of Anthropic's update server.
---

# Update Claude Code (Manual)

The built-in `claude update` command downloads the binary from Anthropic's update server, which is often slow from certain network environments. This skill updates Claude Code by pulling the platform-specific binary directly from the npm registry CDN, then updating the local symlink.

## Detect platform and current version

First, determine the OS/arch and current version:

```bash
uname -s   # Darwin or Linux
uname -m   # arm64 or x86_64
claude --version
```

Map to the npm platform package name:
- macOS Apple Silicon → `@anthropic-ai/claude-code-darwin-arm64`
- macOS Intel → `@anthropic-ai/claude-code-darwin-x64`
- Linux x86_64 → `@anthropic-ai/claude-code-linux-x64`
- Linux ARM64 → `@anthropic-ai/claude-code-linux-arm64`
- Linux musl x86_64 → `@anthropic-ai/claude-code-linux-x64-musl`
- Linux musl ARM64 → `@anthropic-ai/claude-code-linux-arm64-musl`

If unsure which Linux libc, check with `ldd --version 2>&1 | head -1` — musl is explicitly named "musl", otherwise it's glibc (use the non-musl variant).

## Get latest version

```bash
LATEST=$(npm view @anthropic-ai/claude-code version 2>/dev/null)
echo "Latest: $LATEST"
```

If this is slow, use curl directly:
```bash
LATEST=$(curl -sL https://registry.npmjs.org/@anthropic-ai/claude-code/latest | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
```

## Download and install

Replace `PLATFORM_PACKAGE` with the correct package name from above, and `VERSION` with the latest version:

```bash
VERSION=<latest-version>
PKG="@anthropic-ai/claude-code-darwin-arm64"  # adjust for your platform
DEST="$HOME/.local/share/claude/versions/$VERSION"
BIN="$HOME/.local/bin/claude"

# 1. Get the tarball URL from npm registry
URL=$(curl -sL "https://registry.npmjs.org/$PKG/$VERSION" | python3 -c "import json,sys; print(json.load(sys.stdin)['dist']['tarball'])")

# 2. Download and extract
cd /tmp
curl -sL "$URL" -o "claude-$VERSION.tgz"
tar xzf "claude-$VERSION.tgz"

# 3. Install binary
mv package/claude "$DEST"
chmod +x "$DEST"

# 4. Update symlink
ln -sf "$DEST" "$BIN"

# 5. Clean up
rm -rf /tmp/claude-$VERSION.tgz /tmp/package

# 6. Verify
claude --version
```

## Why this is faster

- `claude update` pulls from Anthropic's update endpoint, which may have poor connectivity from some regions.
- npm registry (`registry.npmjs.org`) is served via Cloudflare CDN with global edge nodes, giving better download speeds from most locations.
- The proxy configured in `https_proxy`/`http_proxy` environment variables is respected by `curl` but not necessarily by the `claude` native binary.
