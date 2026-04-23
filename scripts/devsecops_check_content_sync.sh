#!/usr/bin/env bash
set -euo pipefail

# Verifies content alignment between a reference branch and target branches.
# It compares file trees (not commit counts), which avoids false alarms
# caused by different merge commit histories in promotion chains.

REFERENCE_BRANCH="${REFERENCE_BRANCH:-main}"
TARGET_BRANCHES="${TARGET_BRANCHES:-production staging qa develop}"
REMOTE_NAME="${REMOTE_NAME:-origin}"

echo "[sync-check] remote=${REMOTE_NAME} reference=${REFERENCE_BRANCH}"
echo "[sync-check] targets=${TARGET_BRANCHES}"

git fetch --prune "${REMOTE_NAME}" >/dev/null

if ! git show-ref --verify --quiet "refs/remotes/${REMOTE_NAME}/${REFERENCE_BRANCH}"; then
  echo "[sync-check] ERROR: reference branch '${REMOTE_NAME}/${REFERENCE_BRANCH}' not found"
  exit 2
fi

has_drift=0

for branch in ${TARGET_BRANCHES}; do
  full_ref="${REMOTE_NAME}/${branch}"
  if ! git show-ref --verify --quiet "refs/remotes/${full_ref}"; then
    echo "[sync-check] WARN: target branch '${full_ref}' not found, skipping"
    continue
  fi

  if git diff --quiet "${REMOTE_NAME}/${REFERENCE_BRANCH}..${full_ref}"; then
    echo "[sync-check] OK: ${branch} is content-aligned with ${REFERENCE_BRANCH}"
    continue
  fi

  has_drift=1
  echo "[sync-check] DRIFT: ${branch} differs from ${REFERENCE_BRANCH}"
  echo "[sync-check] Changed files (${REFERENCE_BRANCH}..${branch}):"
  git diff --name-status "${REMOTE_NAME}/${REFERENCE_BRANCH}..${full_ref}" | sed 's/^/  - /'
done

if [[ "${has_drift}" -ne 0 ]]; then
  echo "[sync-check] RESULT: drift detected"
  exit 1
fi

echo "[sync-check] RESULT: all target branches are content-aligned"
