#!/usr/bin/env bash
set -euo pipefail

# Create promotion pull requests for the full DevSecOps branch chain.
#
# Usage:
#   bash scripts/devsecops_promotion_chain.sh ORG REPO
#
# Optional env vars:
#   DRY_RUN=true|false (default: false)
#   GH_BIN=/path/to/gh

ORG="${1:?Missing org}"
REPO="${2:?Missing repo}"
DRY_RUN="${DRY_RUN:-false}"

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

create_promotion_pr() {
  local source="$1"
  local target="$2"

  local existing
    existing="$("${GH_BIN}" pr list --repo "${ORG}/${REPO}" --state open --base "$target" --head "$source" --json number --jq '.[0].number // empty')"
  if [[ -n "$existing" ]]; then
    echo "Open PR already exists: #${existing} (${source} -> ${target})"
    return
  fi

  local title="Promote ${source} into ${target}"
  local body="Automated promotion PR from ${source} to ${target}."

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY_RUN] ${GH_BIN} pr create --repo ${ORG}/${REPO} --base ${target} --head ${source} --title \"${title}\""
    return
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

  if [[ $status -eq 0 ]]; then
    echo "$output"
    return
  fi

  # This is expected when source has no new commits compared with target.
  if [[ "$output" == *"No commits between"* ]]; then
    echo "No commits to promote (${source} -> ${target}). Skipping."
    return
  fi

  echo "$output" >&2
  return "$status"
}

# Promotion chain:
# production -> main
# production -> staging
# staging -> qa
# qa -> develop
create_promotion_pr production main
create_promotion_pr production staging
create_promotion_pr staging qa
create_promotion_pr qa develop

echo "Promotion chain processing completed for ${ORG}/${REPO}."
