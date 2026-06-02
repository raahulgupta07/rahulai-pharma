#!/bin/bash
# Install repo-local git hooks (Layer 1 of stale-image defense, Caveat #5).
#
# Idempotent — re-running just refreshes the symlinks.
# Run once after cloning:   bash scripts/install_git_hooks.sh

set -e

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${CURR_DIR}/.." && pwd)"
HOOK_SRC="${CURR_DIR}/hooks"
HOOK_DST="${REPO_ROOT}/.git/hooks"

if [ ! -d "${REPO_ROOT}/.git" ]; then
  echo "[install_git_hooks] error: ${REPO_ROOT}/.git not found — not a git repo" >&2
  exit 1
fi

mkdir -p "${HOOK_DST}"

installed=0
for src in "${HOOK_SRC}"/*; do
  name="$(basename "${src}")"
  dst="${HOOK_DST}/${name}"
  cp "${src}" "${dst}"
  chmod +x "${dst}"
  echo "[install_git_hooks] installed: ${name}"
  installed=$((installed + 1))
done

echo "[install_git_hooks] done — ${installed} hook(s) installed in .git/hooks/"
