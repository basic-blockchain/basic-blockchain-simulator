#!/usr/bin/env bash
set -euo pipefail

# Usage examples:
#   bash scripts/bootstrap_org_rules.sh basic-blockchain
#   bash scripts/bootstrap_org_rules.sh basic-blockchain "repo-a,repo-b" "CI Pull Request / ci" "2" "1" "2" "1" "1"
#
# Optional env vars:
#   DRY_RUN=true  Print gh commands without applying changes.

ORG="${1:?Missing org}"
REPO_LIST="${2:-}"
REQUIRED_CHECK="${3:-CI Pull Request / ci}"
MAIN_APPROVALS="${4:-2}"
DEVELOP_APPROVALS="${5:-1}"
PRODUCTION_APPROVALS="${6:-2}"
STAGING_APPROVALS="${7:-1}"
QA_APPROVALS="${8:-1}"

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

if [[ -z "$REPO_LIST" ]]; then
  mapfile -t repos < <("${GH_BIN}" repo list "$ORG" --limit 200 --json name --jq '.[].name')
else
  IFS=',' read -r -a repos <<< "$REPO_LIST"
fi

for repo in "${repos[@]}"; do
  repo="$(echo "$repo" | xargs)"
  [[ -z "$repo" ]] && continue
  echo "Configuring protections for ${ORG}/${repo}..."
  DRY_RUN="${DRY_RUN:-false}" GH_BIN="${GH_BIN}" bash scripts/bootstrap_github_rules.sh \
    "$ORG" "$repo" "$REQUIRED_CHECK" "$MAIN_APPROVALS" "$DEVELOP_APPROVALS" "$PRODUCTION_APPROVALS" "$STAGING_APPROVALS" "$QA_APPROVALS"
done

echo "Organization bootstrap completed for ${#repos[@]} repositories."
