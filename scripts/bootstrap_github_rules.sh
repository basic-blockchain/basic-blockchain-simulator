#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   bash scripts/bootstrap_github_rules.sh basic-blockchain blockchain-data-model
#   bash scripts/bootstrap_github_rules.sh basic-blockchain blockchain-data-model "ci / detect-language,ci / ci,enforce-merge-policy" "ci / detect-language,ci / ci" "enforce-merge-policy" "" "" "2" "1" "2" "1" "1"
#
# Optional env vars:
#   DRY_RUN=true  Print gh commands without applying changes.

ORG="${1:?Missing org}"
REPO="${2:?Missing repo}"
MAIN_CHECKS="${3:-ci / detect-language,ci / ci,enforce-merge-policy}"
DEVELOP_CHECKS="${4:-ci / detect-language,ci / ci}"
PRODUCTION_CHECKS="${5:-enforce-merge-policy}"
STAGING_CHECKS="${6:-}"
QA_CHECKS="${7:-}"
MAIN_APPROVALS="${8:-2}"
DEVELOP_APPROVALS="${9:-1}"
PRODUCTION_APPROVALS="${10:-2}"
STAGING_APPROVALS="${11:-1}"
QA_APPROVALS="${12:-1}"
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
  local checks_csv="${3:-}"

  local checks_json="null"
  if [[ -n "${checks_csv}" ]]; then
    local contexts=""
    IFS=',' read -r -a checks <<< "${checks_csv}"
    for check in "${checks[@]}"; do
      local trimmed
      trimmed="$(echo "${check}" | xargs)"
      [[ -z "${trimmed}" ]] && continue
      if [[ -n "${contexts}" ]]; then
        contexts+=" , "
      fi
      contexts+="\"${trimmed}\""
    done
    checks_json="{\"strict\":true,\"contexts\":[${contexts}]}"
  fi

  echo "Applying branch protection to ${branch}..."
  local payload
  payload=$(cat <<EOF
{
  "required_status_checks": ${checks_json},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": ${approvals}
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
EOF
)

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY_RUN] ${GH_BIN} api -X PUT repos/${ORG}/${REPO}/branches/${branch}/protection ..."
    return 0
  fi

  printf '%s\n' "${payload}" | "${GH_BIN}" api -X PUT "repos/${ORG}/${REPO}/branches/${branch}/protection" \
    -H "Accept: application/vnd.github+json" --input - >/dev/null
}

# Ensure all branch stages exist remotely.
ensure_branch develop
ensure_branch production
ensure_branch staging
ensure_branch qa

# Enable branch protection on promotion chain branches.
apply_protection main "${MAIN_APPROVALS}" "${MAIN_CHECKS}"
apply_protection develop "${DEVELOP_APPROVALS}" "${DEVELOP_CHECKS}"
apply_protection production "${PRODUCTION_APPROVALS}" "${PRODUCTION_CHECKS}"
apply_protection staging "${STAGING_APPROVALS}" "${STAGING_CHECKS}"
apply_protection qa "${QA_APPROVALS}" "${QA_CHECKS}"

echo "Done. Branch protections are configured for production/main/staging/qa/develop."
