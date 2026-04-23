#!/usr/bin/env bash
set -euo pipefail

# Organization onboarding workflow for DevSecOps controls.
#
# Usage:
#   bash scripts/devsecops_org_onboarding.sh ORG [repo1,repo2,...]
#
# Environment variables:
#   APPLY_CHANGES=true|false   Apply bootstrap between pre/post audits (default: false)
#   OUTPUT_DIR=path            Output folder for reports (default: reports/devsecops)
#   REQUIRED_CHECK=...         Forwarded to bootstrap script
#   MAIN_APPROVALS=2
#   DEVELOP_APPROVALS=1
#   PRODUCTION_APPROVALS=2
#   STAGING_APPROVALS=1
#   QA_APPROVALS=1

ORG="${1:?Missing org}"
REPO_LIST="${2:-}"
APPLY_CHANGES="${APPLY_CHANGES:-false}"
OUTPUT_DIR="${OUTPUT_DIR:-reports/devsecops}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="${OUTPUT_DIR}/onboarding-${ORG}-${TIMESTAMP}.csv"
SUMMARY_FILE="${OUTPUT_DIR}/onboarding-${ORG}-${TIMESTAMP}.summary.txt"

REQUIRED_CHECK="${REQUIRED_CHECK:-CI Pull Request / ci}"
MAIN_APPROVALS="${MAIN_APPROVALS:-2}"
DEVELOP_APPROVALS="${DEVELOP_APPROVALS:-1}"
PRODUCTION_APPROVALS="${PRODUCTION_APPROVALS:-2}"
STAGING_APPROVALS="${STAGING_APPROVALS:-1}"
QA_APPROVALS="${QA_APPROVALS:-1}"

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

mkdir -p "${OUTPUT_DIR}"

resolve_repos() {
  if [[ -n "${REPO_LIST}" ]]; then
    IFS=',' read -r -a repos <<< "${REPO_LIST}"
  else
    mapfile -t repos < <("${GH_BIN}" repo list "${ORG}" --limit 200 --json name,isArchived,isDisabled --jq '.[] | select(.isArchived == false and .isDisabled == false) | .name')
  fi
}

branch_row() {
  local phase="$1"
  local repo="$2"
  local branch="$3"

  if ! "${GH_BIN}" api "repos/${ORG}/${repo}/branches/${branch}" >/dev/null 2>&1; then
    echo "${phase},${repo},${branch},false,false,0,0"
    return
  fi

  if "${GH_BIN}" api "repos/${ORG}/${repo}/branches/${branch}/protection" >/dev/null 2>&1; then
    local approvals checks
    approvals="$("${GH_BIN}" api "repos/${ORG}/${repo}/branches/${branch}/protection" --jq '.required_pull_request_reviews.required_approving_review_count // 0')"
    checks="$("${GH_BIN}" api "repos/${ORG}/${repo}/branches/${branch}/protection" --jq '.required_status_checks.contexts | length // 0')"
    echo "${phase},${repo},${branch},true,true,${approvals},${checks}"
  else
    echo "${phase},${repo},${branch},true,false,0,0"
  fi
}

write_phase() {
  local phase="$1"
  for repo in "${repos[@]}"; do
    repo="$(echo "${repo}" | xargs)"
    [[ -z "${repo}" ]] && continue
    branch_row "${phase}" "${repo}" production >> "${REPORT_FILE}"
    branch_row "${phase}" "${repo}" main >> "${REPORT_FILE}"
    branch_row "${phase}" "${repo}" staging >> "${REPORT_FILE}"
    branch_row "${phase}" "${repo}" qa >> "${REPORT_FILE}"
    branch_row "${phase}" "${repo}" develop >> "${REPORT_FILE}"
  done
}

write_summary() {
  {
    echo "Organization: ${ORG}"
    echo "Report: ${REPORT_FILE}"
    echo "Applied changes: ${APPLY_CHANGES}"
    echo ""
    echo "Rows by phase:"
    awk -F',' 'NR>1 {count[$1]++} END {for (p in count) printf "  %s: %d\n", p, count[p]}' "${REPORT_FILE}" | sort
    echo ""
    echo "Protected rows by phase:"
    awk -F',' 'NR>1 && $5=="true" {count[$1]++} END {for (p in count) printf "  %s: %d\n", p, count[p]}' "${REPORT_FILE}" | sort
  } > "${SUMMARY_FILE}"
}

check_token_scopes() {
  local auth_out
  auth_out="$("${GH_BIN}" auth status -h github.com 2>&1 || true)"
  if ! grep -qi "Token scopes" <<< "${auth_out}"; then
    echo "Warning: token scopes not shown by gh auth status. Verify admin/repo scopes manually." >&2
    return
  fi

  local scopes
  scopes="$(grep -i "Token scopes" <<< "${auth_out}" | sed 's/.*Token scopes: //I')"
  echo "Detected token scopes: ${scopes}"
}

echo "phase,repo,branch,branch_exists,protected,approvals,required_checks" > "${REPORT_FILE}"
check_token_scopes
resolve_repos

if [[ ${#repos[@]} -eq 0 ]]; then
  echo "No repositories found for org ${ORG}." >&2
  exit 1
fi

echo "Collecting PRE onboarding snapshot..."
write_phase pre

if [[ "${APPLY_CHANGES}" == "true" ]]; then
  echo "Applying bootstrap controls..."
  GH_BIN="${GH_BIN}" bash scripts/bootstrap_org_rules.sh \
    "${ORG}" "${REPO_LIST}" "${REQUIRED_CHECK}" "${MAIN_APPROVALS}" "${DEVELOP_APPROVALS}" "${PRODUCTION_APPROVALS}" "${STAGING_APPROVALS}" "${QA_APPROVALS}"
else
  echo "Skipping bootstrap changes (APPLY_CHANGES=${APPLY_CHANGES})."
fi

echo "Collecting POST onboarding snapshot..."
write_phase post
write_summary

echo "Onboarding report generated: ${REPORT_FILE}"
echo "Onboarding summary generated: ${SUMMARY_FILE}"
