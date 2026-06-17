#!/usr/bin/env bash
# rollback.sh — roll the running app back to a previously released image tag.
#
# The release CI (.github/workflows/release.yml) builds every `v*` git tag into
#   ghcr.io/<owner>/citypharma:<version>   (+ :latest)
# so any past version can be redeployed WITHOUT a rebuild — just pull + recreate.
#
# Usage:
#   scripts/rollback.sh 1.48.0                 # roll back to a released version
#   scripts/rollback.sh 1.48.0 --code          # also checkout the matching git tag
#   IMAGE_NAME=ghcr.io/raahulgupta07/citypharma scripts/rollback.sh 1.48.0
#
# Requires: docker login ghcr.io (read:packages) if the package is private.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: scripts/rollback.sh <version> [--code]" >&2
  echo "released tags:" >&2
  git tag --list 'v*' | tail -10 >&2
  exit 2
fi
VERSION="${VERSION#v}"                      # accept v1.48.0 or 1.48.0
IMAGE_NAME="${IMAGE_NAME:-ghcr.io/raahulgupta07/citypharma}"
REF="${IMAGE_NAME}:${VERSION}"

echo "==> rollback target: ${REF}"

# 1. Pull the old image (fail clearly if that version was never published).
if ! docker pull "${REF}"; then
  echo "ERROR: ${REF} not found in registry." >&2
  echo "       Either the version is wrong or CI never built it. Local images:" >&2
  docker images "${IMAGE_NAME%/*}/citypharma" --format '  {{.Repository}}:{{.Tag}}' 2>/dev/null || true
  docker images citypharma --format '  {{.Repository}}:{{.Tag}}' 2>/dev/null || true
  exit 1
fi

# 2. Optionally move the working tree to the matching source tag (read-only deploy
#    boxes can skip this; the image alone is enough to run).
if [[ "${2:-}" == "--code" ]]; then
  echo "==> git checkout v${VERSION}"
  git fetch --tags --quiet || true
  git checkout "v${VERSION}"
fi

# 3. Point compose at the rolled-back image and recreate ONLY the app container.
#    DB / redis / pgbouncer stay up — we never touch data on a rollback.
export IMAGE_NAME IMAGE_TAG="${VERSION}"
echo "==> deploying ${IMAGE_NAME}:${IMAGE_TAG}"
docker compose -f compose.yaml up -d --no-build --force-recreate dash-api

# 4. Wait for health.
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/api/health || true)
  if [[ "$code" == "200" ]]; then echo "==> health 200 — rolled back to ${VERSION}"; break; fi
  sleep 2
done

echo
echo "Rolled back to ${VERSION}."
echo "!! DB migrations are forward-only — this did NOT revert any schema change."
echo "   If a breaking migration shipped after ${VERSION}, see docs/ROLLBACK.md."
echo "   Persist the choice by setting IMAGE_TAG=${VERSION} in .env."
