# DevSecOps Promotion Strategy — Upward Flow

## Overview

This document describes the automated promotion pipeline that enables single-developer DevSecOps workflows while maintaining rigorous CI/CD gates and audit trails for the Blockchain Simulator backend.

## Problem Statement

The traditional GitFlow model with external reviewer requirements creates bottlenecks in single-developer projects. This strategy solves that by:

- ✅ Enabling auto-merge when CI validates all checks
- ✅ Maintaining mandatory CI/CD gates
- ✅ Enforcing conversation resolution
- ✅ Preserving full audit trails
- ✅ Preventing force-push vulnerabilities

## Promotion Direction: Upward Flow

The promotion chain moves **upward** from development to production:

```
develop ──► qa ──► staging ──► production ──► main
   ▲                                              │
   └──────────────────────────────────────────────┘
              (Each step automated)
```

### Flow Explanation

| Stage | Purpose | CI Gates | Auto-Merge |
|-------|---------|----------|-----------|
| `develop` | Backend development & integration | ✅ Required | ✅ Yes (0 reviewers) |
| `qa` | QA environment (full test suite) | ✅ Required | ✅ Yes (0 reviewers) |
| `staging` | Pre-production environment | ✅ Required | ✅ Yes (0 reviewers) |
| `production` | Production-ready code | ✅ Required | ✅ Yes (0 reviewers) |
| `main` | Release tag target (stable versions) | ✅ Required | ✅ Yes (0 reviewers) |

## CI Gates for Simulator

The `ci.yml` workflow validates:

1. **Python linting** — code style via flake8 / pylint
2. **Type checking** — mypy strict mode
3. **Unit tests** — pytest with coverage >80%
4. **Integration tests** — PostgreSQL-dependent tests (when DB_URL set)
5. **Security scanning** — SAST checks (CodeQL, etc.)

All must pass before auto-merge is allowed.

## Implementation

### Scripts

#### `devsecops_promotion_chain.sh`
Creates promotion PRs in upward direction. Run after merging to develop:

```bash
bash scripts/devsecops_promotion_chain.sh basic-blockchain basic-blockchain-simulator
```

**Dry-run mode** (test without creating PRs):
```bash
DRY_RUN=true bash scripts/devsecops_promotion_chain.sh basic-blockchain basic-blockchain-simulator
```

#### `bootstrap_branch_protections.sh`
Applies branch protections from JSON configuration files:

```bash
# Preview changes
bash scripts/bootstrap_branch_protections.sh basic-blockchain basic-blockchain-simulator --dry-run

# Apply protections
GH_BIN="/c/Program Files/GitHub CLI/gh.exe" \
  bash scripts/bootstrap_branch_protections.sh basic-blockchain basic-blockchain-simulator
```

### Protection Configuration Files

Protection rules are version-controlled in JSON files:

- `protection_develop.json` — develop branch rules
- `protection_qa.json` — qa branch rules  
- `protection_staging.json` — staging branch rules
- `protection_production.json` — production branch rules
- `protection_main.json` — main branch rules

Each specifies:
- **Required status checks** (CI must pass)
- **Required conversation resolution**
- **Code owner review requirement** (set to false for auto-merge)
- **Required approving review count** (set to 0 for auto-merge)
- **Dismiss stale reviews** (auto-dismiss outdated reviews)

## Developer Workflow

### Normal Feature Development

```bash
# 1. Create feature branch from develop
git checkout -b feature/add-consensus-module develop

# 2. Implement feature with commits
git add .
git commit -m "feat(consensus): add PoW difficulty adjustment"

# 3. Push and create PR targeting develop
git push origin feature/add-consensus-module
gh pr create --base develop --head feature/add-consensus-module --fill

# 4. CI runs automatically
# - Lint: ✅ passes
# - Type check: ✅ passes
# - Unit tests: ✅ all green
# - Integration tests: ✅ all green
# - Security: ✅ no issues

# 5. PR auto-merges (no manual review needed)
#    All CI gates passed ✅
#    Conversation resolved ✅
```

### Manual Promotion (if needed)

```bash
# After feature is merged to develop
bash scripts/devsecops_promotion_chain.sh basic-blockchain basic-blockchain-simulator

# Automatic flow:
# develop → qa (auto-merge when CI passes)
#   ↓
# qa → staging (auto-merge when CI passes)
#   ↓
# staging → production (auto-merge when CI passes)
#   ↓
# production → main (auto-merge when CI passes + signed tag)
```

## Why This Works for Single Developer

1. **CI is the real reviewer** — automated tests catch more issues than manual review
2. **Conversation resolution required** — prevents accidental merges with open discussions
3. **No force-push allowed** — history immutable and auditable
4. **Git maintains attribution** — author and timestamp preserved
5. **Full trail in GitHub** — every PR/commit/review logged

## Comparison: Before vs After

### Before (Blocked by Reviewer Requirement)
```
Feature → develop PR ✋ BLOCKED (waiting for reviewer)
          ├─ CI passed ✅
          ├─ All tests green ✅
          ├─ Code is ready ✅
          └─ But cannot merge own PR ❌
```

### After (Auto-Merge with CI Gates)
```
Feature → develop PR ✅ AUTO-MERGES
          ├─ Lint passed ✅
          ├─ Type check passed ✅
          ├─ Tests passed ✅
          ├─ Security passed ✅
          ├─ Conversation resolved ✅
          └─ Auto-merges immediately ✅

develop → qa → staging → production → main
  ✅      ✅    ✅         ✅        ✅ (all auto-merge)
```

## Safety Guarantees

| Guarantee | Mechanism | Evidence |
|-----------|-----------|----------|
| **No bad code reaches production** | Mandatory CI/CD checks | Test suite report in PR |
| **Discussion enforced** | Conversation resolution required | GitHub enforces unresolved comment blocking |
| **History protected** | No force-push allowed | GitHub API enforces `allow_force_pushes: false` |
| **Full audit trail** | Every PR/commit in GitHub | GitHub PR history + commit graph |
| **Linear progression** | One-way upward promotion | `devsecops_promotion_chain.sh` only goes up |
| **No accidental push** | Branch protection rules enforced by GitHub API | JSON configuration version-controlled |

## Testing the Strategy

### Dry Run (Recommended First Step)

```bash
# Test without creating actual PRs
DRY_RUN=true bash scripts/devsecops_promotion_chain.sh basic-blockchain basic-blockchain-simulator

# Output shows what would be created:
# [DRY_RUN] gh pr create --repo basic-blockchain/basic-blockchain-simulator ...
# [DRY_RUN] gh pr create --repo basic-blockchain/basic-blockchain-simulator ...
# ... etc
```

### Live Execution

```bash
# Run actual promotion
bash scripts/devsecops_promotion_chain.sh basic-blockchain basic-blockchain-simulator

# Monitor PRs:
# 1. develop → qa: https://github.com/basic-blockchain/basic-blockchain-simulator/pulls
# 2. qa → staging
# 3. staging → production
# 4. production → main
```

## Troubleshooting

### PR Creation Fails: "No commits between..."
**Normal behavior.** The source branch has no new commits compared to target. Script handles gracefully.

### PR Won't Auto-Merge
Check:
1. **CI status** — must be ✅ green (all workflow checks pass)
2. **Conversation resolution** — all comments must be resolved
3. **Protection rules** — verify JSON files are current (`bootstrap_branch_protections.sh`)

### Need to Override Protections
Only GitHub organization admins can override. Contact DevOps team if needed.

## Related Documentation

- [Architecture](../architecture.md) — System design and layers
- [Business Rules](../business-rules.md) — Enforced constraints
- [API Reference](../api-reference.md) — Endpoint specification
- [Flows](../flows.md) — Operation diagrams

## Version History

- **v1.0** (2026-05-14) — Initial implementation with upward promotion flow for simulator backend
