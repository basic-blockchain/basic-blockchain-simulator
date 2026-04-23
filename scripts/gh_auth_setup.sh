#!/usr/bin/env bash
set -euo pipefail

# GitHub CLI auth helper for Windows + Git Bash environments.
#
# Usage:
#   bash scripts/gh_auth_setup.sh status
#   bash scripts/gh_auth_setup.sh login
#   bash scripts/gh_auth_setup.sh enable-path

usage() {
  cat <<'EOF'
Usage:
  bash scripts/gh_auth_setup.sh status
  bash scripts/gh_auth_setup.sh login
  bash scripts/gh_auth_setup.sh enable-path

Commands:
  status      Show gh auth status
  login       Start interactive gh auth login
  enable-path Add GitHub CLI path to ~/.bashrc if missing
EOF
}

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

ensure_gh() {
  if ! GH_BIN="$(resolve_gh_bin)"; then
    echo "gh CLI is not available in this shell." >&2
    echo "Install with: winget install --id GitHub.cli -e --source winget" >&2
    exit 1
  fi
}

enable_path() {
  local line='export PATH="$PATH:/c/Program Files/GitHub CLI"'
  if [[ ! -f "$HOME/.bashrc" ]]; then
    printf '%s\n' "$line" > "$HOME/.bashrc"
    echo "Created ~/.bashrc and added GitHub CLI path."
    return
  fi

  if grep -Fq '/c/Program Files/GitHub CLI' "$HOME/.bashrc"; then
    echo "GitHub CLI path already present in ~/.bashrc"
    return
  fi

  printf '\n%s\n' "$line" >> "$HOME/.bashrc"
  echo "Added GitHub CLI path to ~/.bashrc"
  echo "Run: source ~/.bashrc"
}

main() {
  local command="${1:-}"
  if [[ -z "$command" ]]; then
    usage
    exit 1
  fi

  case "$command" in
    status)
      ensure_gh
      "${GH_BIN}" auth status
      ;;
    login)
      ensure_gh
      "${GH_BIN}" auth login
      ;;
    enable-path)
      enable_path
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
