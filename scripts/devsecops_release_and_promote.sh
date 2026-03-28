#!/usr/bin/env bash
set -euo pipefail

# Full release and promotion automation for the GitFlow chain.
#
# Flow:
# 1) create release/* from SOURCE_BRANCH
# 2) open + merge release/* -> production
# 3) open + merge production -> main
# 4) open + merge production -> staging
# 5) open + merge staging -> qa
# 6) open + merge qa -> develop
#
# Usage:
#   bash scripts/devsecops_release_and_promote.sh ORG REPO [SOURCE_BRANCH]
#
# Optional env vars:
#   GH_BIN=/path/to/gh
#   RELEASE_PREFIX=release/auto
#   MAX_WAIT_SECONDS=1800
#   POLL_SECONDS=15

ORG="${1:?Missing org}"
REPO="${2:?Missing repo}"
SOURCE_BRANCH="${3:-develop}"
RELEASE_PREFIX="${RELEASE_PREFIX:-release/auto}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
POLL_SECONDS="${POLL_SECONDS:-15}"

resolve_gh_bin() {
  if [[ -n "${GH_BIN:-}" ]]; then
    echo "${GH_BIN}"
    return 0
  fi

  if command -v gh >/dev/null 2>&1; then
    command -v gh
    return 0
  fi

  if [[ -x "/c/Program Files/GitHub CLI/gh.exe" ]]; then
    echo "/c/Program Files/GitHub CLI/gh.exe"
    return 0
  fi

  if [[ -x "/c/Users/${USERNAME:-}/AppData/Local/Programs/GitHub CLI/gh.exe" ]]; then
    echo "/c/Users/${USERNAME}/AppData/Local/Programs/GitHub CLI/gh.exe"
    return 0
  fi

  return 1
}

if ! GH_BIN="$(resolve_gh_bin)"; then
  echo "gh CLI is required. Install from https://cli.github.com/" >&2
  exit 1
fi

if ! "${GH_BIN}" auth status >/dev/null 2>&1; then
  echo "gh CLI is not authenticated. Run: bash scripts/gh_auth_setup.sh login" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required." >&2
  exit 1
fi

find_open_pr() {
  local source="$1"
  local target="$2"
  "${GH_BIN}" pr list \
    --repo "${ORG}/${REPO}" \
    --state open \
    --base "$target" \
    --head "$source" \
    --json number \
    --jq '.[0].number // empty'
}

create_pr_if_needed() {
  local source="$1"
  local target="$2"
  local title="Promote ${source} into ${target}"
  local body="Automated promotion PR from ${source} to ${target}."

  local existing
  existing="$(find_open_pr "$source" "$target")"
  if [[ -n "$existing" ]]; then
    echo "$existing"
    return 0
  fi

  local output
  set +e
  output="$("${GH_BIN}" pr create \
    --repo "${ORG}/${REPO}" \
    --base "$target" \
    --head "$source" \
    --title "$title" \
    --body "$body" 2>&1)"
  local status=$?
  set -e

  if [[ $status -ne 0 ]]; then
    if [[ "$output" == *"No commits between"* ]]; then
      echo ""
      return 0
    fi
    echo "$output" >&2
    return "$status"
  fi

  find_open_pr "$source" "$target"
}

merge_pr_with_retry() {
  local pr_number="$1"
  local started_at
  started_at="$(date +%s)"

  while true; do
    local state
    state="$("${GH_BIN}" pr view "$pr_number" --repo "${ORG}/${REPO}" --json state --jq '.state')"
    if [[ "$state" == "MERGED" || "$state" == "CLOSED" ]]; then
      echo "PR #${pr_number} already closed (${state})."
      return 0
    fi

    set +e
    local output
    output="$("${GH_BIN}" pr merge "$pr_number" --repo "${ORG}/${REPO}" --merge 2>&1)"
    local status=$?
    set -e

    if [[ $status -eq 0 ]]; then
      echo "$output"
      return 0
    fi

    local now
    now="$(date +%s)"
    local elapsed=$((now - started_at))
    if (( elapsed >= MAX_WAIT_SECONDS )); then
      echo "Timed out while waiting to merge PR #${pr_number}." >&2
      echo "$output" >&2
      return 1
    fi

    echo "Waiting for PR #${pr_number} to become mergeable... (${elapsed}s elapsed)"
    sleep "$POLL_SECONDS"
  done
}

sync_branch() {
  local branch="$1"
  git checkout "$branch" >/dev/null 2>&1
  git pull --ff-only origin "$branch" >/dev/null
}

echo "[1/6] Syncing source branch: ${SOURCE_BRANCH}"
git fetch origin --prune
sync_branch "$SOURCE_BRANCH"

release_branch="${RELEASE_PREFIX}-$(date -u +%Y%m%dT%H%M%SZ)"
echo "[2/6] Creating release branch: ${release_branch}"
git checkout -b "$release_branch" >/dev/null
git push -u origin "$release_branch" >/dev/null

release_pr="$(create_pr_if_needed "$release_branch" production)"
if [[ -z "$release_pr" ]]; then
  echo "No changes to release from ${release_branch} into production."
else
  echo "[3/6] Merging release PR #${release_pr} (${release_branch} -> production)"
  merge_pr_with_retry "$release_pr"
fi

# Keep using the existing promotion helper to create whatever is currently possible.
echo "[4/6] Triggering promotion chain PR creation"
bash scripts/devsecops_promotion_chain.sh "$ORG" "$REPO"

pr_prod_main="$(find_open_pr production main)"
if [[ -n "$pr_prod_main" ]]; then
  echo "[5/6] Merging PR #${pr_prod_main} (production -> main)"
  merge_pr_with_retry "$pr_prod_main"
fi

pr_prod_staging="$(find_open_pr production staging)"
if [[ -n "$pr_prod_staging" ]]; then
  echo "[5/6] Merging PR #${pr_prod_staging} (production -> staging)"
  merge_pr_with_retry "$pr_prod_staging"
fi

# Downstream PRs depend on previous merges; create again after each stage.
bash scripts/devsecops_promotion_chain.sh "$ORG" "$REPO"
pr_staging_qa="$(find_open_pr staging qa)"
if [[ -n "$pr_staging_qa" ]]; then
  echo "[6/6] Merging PR #${pr_staging_qa} (staging -> qa)"
  merge_pr_with_retry "$pr_staging_qa"
fi

bash scripts/devsecops_promotion_chain.sh "$ORG" "$REPO"
pr_qa_develop="$(find_open_pr qa develop)"
if [[ -n "$pr_qa_develop" ]]; then
  echo "[6/6] Merging PR #${pr_qa_develop} (qa -> develop)"
  merge_pr_with_retry "$pr_qa_develop"
fi

echo "Delivery automation finished for ${ORG}/${REPO}."
