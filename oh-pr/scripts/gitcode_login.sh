#!/usr/bin/env bash
# Reuse the GitCode PAT stored in git's credential store to log oh-gc in.
# oh-gc auth login's --token flag is unreliable (it still prompts), so we feed
# the token via stdin. Safe to run repeatedly: oh-gc caches the session.
set -euo pipefail

CRED="${HOME}/.git-credentials"
if [[ ! -f "$CRED" ]]; then
  echo "no ~/.git-credentials found; run 'git push' once or set one manually" >&2
  exit 1
fi

TOKEN="$(grep -o 'https://[^@]*@gitcode.com' "$CRED" | head -1 | sed -E 's#https://[^:]+:([^@]+)@gitcode.com#\1#')"
if [[ -z "$TOKEN" ]]; then
  echo "could not extract gitcode token from $CRED" >&2
  exit 1
fi

printf '%s\n' "$TOKEN" | oh-gc auth login 2>&1 | tail -1
