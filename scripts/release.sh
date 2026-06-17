#!/usr/bin/env bash
# release.sh — cut a versioned release: verify, tag, push. CI does the image build.
#
# Flow:
#   1. you bump VERSION + CHANGELOG.md + docs/CHANGELOG.json and commit
#   2. run scripts/release.sh
#   3. it tags vX.Y.Z (from VERSION) and pushes — .github/workflows/release.yml
#      then builds + pushes ghcr.io/<owner>/citypharma:X.Y.Z (+ :latest)
#
# Usage:
#   scripts/release.sh            # tag from current VERSION, push
#   scripts/release.sh --dry-run  # show what it would do, change nothing
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
VERSION="$(tr -d '[:space:]' < VERSION)"
TAG="v${VERSION}"

echo "==> version: ${VERSION}  tag: ${TAG}"

# Guard 1: working tree must be clean (release exactly what's committed).
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: uncommitted changes. Commit VERSION + changelog first." >&2
  git status -s >&2
  exit 1
fi

# Guard 2: tag must not already exist.
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  echo "ERROR: tag ${TAG} already exists. Bump VERSION." >&2
  exit 1
fi

# Guard 3: changelog must mention this version (cheap "did you forget" check).
if ! grep -q "${VERSION}" CHANGELOG.md; then
  echo "WARN: ${VERSION} not found in CHANGELOG.md — add release notes." >&2
fi

# Guard 4: quick local sanity — python compiles. (Full gate runs in CI.)
python3 -m py_compile dash/instructions.py dash/tools/pharma_shop_tool.py 2>/dev/null \
  && echo "==> py_compile OK" || { echo "ERROR: py_compile failed" >&2; exit 1; }

if [[ "$DRY" == "1" ]]; then
  echo "[dry-run] would: git tag ${TAG} && git push origin main --tags"
  exit 0
fi

git tag -a "${TAG}" -m "Release ${TAG}"
git push origin HEAD
git push origin "${TAG}"

echo
echo "Pushed ${TAG}. Watch the build:"
echo "  https://github.com/raahulgupta07/rahulai-pharma/actions"
echo "When green, deploy: IMAGE_TAG=${VERSION} docker compose pull dash-api && docker compose up -d dash-api"
