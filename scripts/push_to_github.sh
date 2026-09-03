#!/usr/bin/env bash
# Push to hupender/GauTrack using PAT in repo-root .gh-token (gitignored).
# Create token while logged into GitHub as hupender → repo scope.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_FILE="$ROOT/.gh-token"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "Create $TOKEN_FILE with one line: your hupender PAT (ghp_...)" >&2
  exit 2
fi
TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")
if [ -z "$TOKEN" ] || [ "$TOKEN" = "ghp_REPLACE_WITH_YOUR_HUPENDER_PAT" ]; then
  echo "Edit $TOKEN_FILE and replace the placeholder with your real PAT." >&2
  exit 2
fi
case "$TOKEN" in ghp_*|github_pat_*) ;; *)
  echo "Token should start with ghp_ or github_pat_" >&2
  exit 2
  ;;
esac

cd "$ROOT"
echo "[push] pushing master to hupender/GauTrack..."
# x-access-token avoids username/credential-helper picking the wrong GitHub account.
git -c credential.helper= push "https://x-access-token:${TOKEN}@github.com/hupender/GauTrack.git" master
git branch --set-upstream-to=origin/master master 2>/dev/null || true
echo "[push] done. You can delete .gh-token if you like."
