#!/usr/bin/env bash
set -euo pipefail

# Organization-level DevSecOps operations using GitHub CLI.
#
# Commands:
#   bootstrap ORG [repo1,repo2,...]
#   audit ORG [repo1,repo2,...]
#   promote-pr ORG REPO SOURCE TARGET
#   promote-chain ORG REPO
#   onboard ORG [repo1,repo2,...]
#   auth-status
#   auth-login
#   auth-enable-path
#
# Environment variables for bootstrap:
#   REQUIRED_CHECK (default: CI Pull Request / ci)
#   MAIN_APPROVALS (default: 2)
#   DEVELOP_APPROVALS (default: 1)
#   PRODUCTION_APPROVALS (default: 2)
#   STAGING_APPROVALS (default: 1)
#   QA_APPROVALS (default: 1)
#   DRY_RUN=true (print commands without applying)

usage() {
  cat <<'EOF'
Usage:
  bash scripts/devsecops_org_cli.sh bootstrap ORG [repo1,repo2,...]
  bash scripts/devsecops_org_cli.sh audit ORG [repo1,repo2,...]
  bash scripts/devsecops_org_cli.sh promote-pr ORG REPO SOURCE TARGET
  bash scripts/devsecops_org_cli.sh promote-chain ORG REPO
  bash scripts/devsecops_org_cli.sh onboard ORG [repo1,repo2,...]
  bash scripts/devsecops_org_cli.sh auth-status
  bash scripts/devsecops_org_cli.sh auth-login
  bash scripts/devsecops_org_cli.sh auth-enable-path

Examples:
  DRY_RUN=true bash scripts/devsecops_org_cli.sh bootstrap basic-blockchain
  bash scripts/devsecops_org_cli.sh audit basic-blockchain "repo-a,repo-b"
  bash scripts/devsecops_org_cli.sh promote-pr basic-blockchain blockchain-data-model qa develop
  bash scripts/devsecops_org_cli.sh promote-chain basic-blockchain blockchain-data-model
  APPLY_CHANGES=true bash scripts/devsecops_org_cli.sh onboard basic-blockchain
  bash scripts/devsecops_org_cli.sh auth-login
EOF
}

require_gh() {
  if [[ -n "${GH_BIN:-}" ]]; then
    return 0
  fi

  if command -v gh >/dev/null 2>&1; then
    GH_BIN="$(command -v gh)"
    return 0
  fi

  if [[ -x "/c/Program Files/GitHub CLI/gh.exe" ]]; then
    GH_BIN="/c/Program Files/GitHub CLI/gh.exe"
    return 0
  fi

  if [[ -x "/c/Users/${USERNAME:-}/AppData/Local/Programs/GitHub CLI/gh.exe" ]]; then
    GH_BIN="/c/Users/${USERNAME}/AppData/Local/Programs/GitHub CLI/gh.exe"
    return 0
  fi

  echo "gh CLI is required. Install from https://cli.github.com/" >&2
  exit 1
}

require_auth() {
  if ! "${GH_BIN}" auth status >/dev/null 2>&1; then
    echo "gh CLI is not authenticated. Run: gh auth login" >&2
    exit 1
  fi
}

require_gh_or_fail() {
  require_gh
  if [[ -z "${GH_BIN:-}" ]]; then
    echo "gh CLI is required. Install from https://cli.github.com/" >&2
    exit 1
  fi
  require_auth
}

resolve_repos() {
  local org="$1"
  local list="${2:-}"
  if [[ -n "$list" ]]; then
    IFS=',' read -r -a _repos <<< "$list"
  else
    mapfile -t _repos < <("${GH_BIN}" repo list "$org" --limit 200 --json name,isArchived,isDisabled --jq '.[] | select(.isArchived == false and .isDisabled == false) | .name')
  fi
}

cmd_bootstrap() {
  local org="${1:?Missing org}"
  local repos="${2:-}"

  local required_check="${REQUIRED_CHECK:-CI Pull Request / ci}"
  local main_approvals="${MAIN_APPROVALS:-2}"
  local develop_approvals="${DEVELOP_APPROVALS:-1}"
  local production_approvals="${PRODUCTION_APPROVALS:-2}"
  local staging_approvals="${STAGING_APPROVALS:-1}"
  local qa_approvals="${QA_APPROVALS:-1}"

  bash scripts/bootstrap_org_rules.sh \
    "$org" "$repos" "$required_check" "$main_approvals" "$develop_approvals" "$production_approvals" "$staging_approvals" "$qa_approvals"
}

audit_repo_branch() {
  local org="$1"
  local repo="$2"
  local branch="$3"

  if ! "${GH_BIN}" api "repos/${org}/${repo}/branches/${branch}" >/dev/null 2>&1; then
    echo "${repo},${branch},MISSING_BRANCH,NO_PROTECTION"
    return
  fi

  if "${GH_BIN}" api "repos/${org}/${repo}/branches/${branch}/protection" >/dev/null 2>&1; then
    local approvals
    approvals="$("${GH_BIN}" api "repos/${org}/${repo}/branches/${branch}/protection" --jq '.required_pull_request_reviews.required_approving_review_count // 0')"
    echo "${repo},${branch},OK,approvals=${approvals}"
  else
    echo "${repo},${branch},OK,NO_PROTECTION"
  fi
}

cmd_audit() {
  local org="${1:?Missing org}"
  local repos="${2:-}"
  resolve_repos "$org" "$repos"

  echo "repo,branch,status,protection"
  for repo in "${_repos[@]}"; do
    repo="$(echo "$repo" | xargs)"
    [[ -z "$repo" ]] && continue
    audit_repo_branch "$org" "$repo" production
    audit_repo_branch "$org" "$repo" main
    audit_repo_branch "$org" "$repo" staging
    audit_repo_branch "$org" "$repo" qa
    audit_repo_branch "$org" "$repo" develop
  done
}

cmd_promote_pr() {
  local org="${1:?Missing org}"
  local repo="${2:?Missing repo}"
  local source="${3:?Missing source branch}"
  local target="${4:?Missing target branch}"

  local existing
  existing="$("${GH_BIN}" pr list --repo "${org}/${repo}" --state open --base "$target" --head "$source" --json number --jq '.[0].number // empty')"
  if [[ -n "$existing" ]]; then
    echo "Open PR already exists: #${existing} (${source} -> ${target})"
    return
  fi

  "${GH_BIN}" pr create \
    --repo "${org}/${repo}" \
    --base "$target" \
    --head "$source" \
    --title "Promote ${source} into ${target}" \
    --body "Automated by scripts/devsecops_org_cli.sh for promotion ${source} -> ${target}."
}

cmd_onboard() {
  local org="${1:?Missing org}"
  local repos="${2:-}"

  GH_BIN="${GH_BIN}" APPLY_CHANGES="${APPLY_CHANGES:-false}" OUTPUT_DIR="${OUTPUT_DIR:-reports/devsecops}" \
    REQUIRED_CHECK="${REQUIRED_CHECK:-CI Pull Request / ci}" MAIN_APPROVALS="${MAIN_APPROVALS:-2}" \
    DEVELOP_APPROVALS="${DEVELOP_APPROVALS:-1}" PRODUCTION_APPROVALS="${PRODUCTION_APPROVALS:-2}" \
    STAGING_APPROVALS="${STAGING_APPROVALS:-1}" QA_APPROVALS="${QA_APPROVALS:-1}" \
    bash scripts/devsecops_org_onboarding.sh "${org}" "${repos}"
}

cmd_promote_chain() {
  local org="${1:?Missing org}"
  local repo="${2:?Missing repo}"
  GH_BIN="${GH_BIN}" DRY_RUN="${DRY_RUN:-false}" bash scripts/devsecops_promotion_chain.sh "${org}" "${repo}"
}

cmd_auth_status() {
  GH_BIN="${GH_BIN:-}" bash scripts/gh_auth_setup.sh status
}

cmd_auth_login() {
  GH_BIN="${GH_BIN:-}" bash scripts/gh_auth_setup.sh login
}

cmd_auth_enable_path() {
  bash scripts/gh_auth_setup.sh enable-path
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi

  local command="$1"
  shift

  case "$command" in
    bootstrap|audit|onboard)
      if [[ $# -lt 1 ]]; then
        usage
        exit 1
      fi
      ;;
    promote-pr)
      if [[ $# -lt 4 ]]; then
        usage
        exit 1
      fi
      ;;
    promote-chain)
      if [[ $# -lt 2 ]]; then
        usage
        exit 1
      fi
      ;;
  esac

  case "$command" in
    bootstrap)
      require_gh_or_fail
      cmd_bootstrap "$@"
      ;;
    audit)
      require_gh_or_fail
      cmd_audit "$@"
      ;;
    promote-pr)
      require_gh_or_fail
      cmd_promote_pr "$@"
      ;;
    promote-chain)
      require_gh_or_fail
      cmd_promote_chain "$@"
      ;;
    onboard)
      require_gh_or_fail
      cmd_onboard "$@"
      ;;
    auth-status)
      cmd_auth_status
      ;;
    auth-login)
      cmd_auth_login
      ;;
    auth-enable-path)
      cmd_auth_enable_path
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
