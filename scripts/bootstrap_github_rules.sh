#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash scripts/bootstrap_github_rules.sh basic-blockchain blockchain-data-model
#   bash scripts/bootstrap_github_rules.sh basic-blockchain blockchain-data-model "CI Pull Request / ci" "2" "1" "2" "1" "1"
#
# Optional env vars:
#   DRY_RUN=true  Print gh commands without applying changes.

ORG="${1:?Missing org}"
REPO="${2:?Missing repo}"
REQUIRED_CHECK="${3:-CI Pull Request / ci}"
MAIN_APPROVALS="${4:-2}"
DEVELOP_APPROVALS="${5:-1}"
PRODUCTION_APPROVALS="${6:-2}"
STAGING_APPROVALS="${7:-1}"
QA_APPROVALS="${8:-1}"
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
  echo "gh CLI is not authenticated. Run: gh auth login" >&2
  exit 1
fi

run_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY_RUN] $*"
    return 0
  fi
  "$@"
}

ensure_branch() {
  local branch="$1"
  if ! "${GH_BIN}" api "repos/${ORG}/${REPO}/branches/${branch}" >/dev/null 2>&1; then
    echo "Creating ${branch} branch from main..."
    local main_sha
    main_sha="$("${GH_BIN}" api "repos/${ORG}/${REPO}/git/ref/heads/main" --jq '.object.sha')"
    run_cmd "${GH_BIN}" api "repos/${ORG}/${REPO}/git/refs" -f ref="refs/heads/${branch}" -f sha="${main_sha}" >/dev/null
  fi
}

apply_protection() {
  local branch="$1"
  local approvals="$2"
  shift 2
  local extra_checks=("$@")

  echo "Applying branch protection to ${branch}..."
  local cmd=(
    "${GH_BIN}" api -X PUT "repos/${ORG}/${REPO}/branches/${branch}/protection"
    -H "Accept: application/vnd.github+json"
    -f required_status_checks.strict=true
    -f "required_status_checks.contexts[]=${REQUIRED_CHECK}"
    -f "required_status_checks.contexts[]=Security PR Checks / secret-scan"
    -f enforce_admins=true
    -f required_pull_request_reviews.dismiss_stale_reviews=true
    -f required_pull_request_reviews.require_code_owner_reviews=true
    -f "required_pull_request_reviews.required_approving_review_count=${approvals}"
    -f restrictions=
  )

  for check in "${extra_checks[@]}"; do
    [[ -z "${check}" ]] && continue
    cmd+=( -f "required_status_checks.contexts[]=${check}" )
  done

  run_cmd "${cmd[@]}" >/dev/null
}

# Ensure all branch stages exist remotely.
ensure_branch develop
ensure_branch production
ensure_branch staging
ensure_branch qa

# Enable branch protection on promotion chain branches.
apply_protection main "${MAIN_APPROVALS}" "Merge Policy Guard / enforce-merge-policy"
apply_protection develop "${DEVELOP_APPROVALS}"
apply_protection production "${PRODUCTION_APPROVALS}" "Merge Policy Guard / enforce-merge-policy"
apply_protection staging "${STAGING_APPROVALS}"
apply_protection qa "${QA_APPROVALS}"

echo "Done. Branch protections are configured for production/main/staging/qa/develop."
