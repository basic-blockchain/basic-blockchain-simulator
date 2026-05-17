"""Permission catalog and 3-level RBAC resolution (Phase I.2).

This module is pure domain — no Quart, no psycopg2 imports — so the API
layer can install a `@require_permission(...)` decorator on top, and the
persistence layer can persist overrides without round-tripping through
HTTP code.

The 3-level resolution matches `blockchain-data-model/domain/auth.py`:

  1. user_permissions[user_id]  (highest priority, direct grant per user)
  2. role_permissions[role]     (override per role, replaces the default)
  3. ROLE_PERMISSIONS[role]     (hardcoded baseline from this file)

Returning True at any level short-circuits. If no level matches, the
permission is denied.
"""

from __future__ import annotations

from enum import Enum

from domain.auth import Role


class Permission(str, Enum):
    """Every action the simulator authorises through RBAC.

    The values must match the seed inserted by V009 — adding a new entry
    here also requires extending the migration so persistence-side
    overrides remain referentially valid.
    """

    # User management (Phase I.2)
    CREATE_USER = "CREATE_USER"
    VIEW_USERS = "VIEW_USERS"
    UPDATE_USER = "UPDATE_USER"
    BAN_USER = "BAN_USER"
    UNBAN_USER = "UNBAN_USER"
    DELETE_USER = "DELETE_USER"
    RESTORE_USER = "RESTORE_USER"
    ASSIGN_ROLE = "ASSIGN_ROLE"
    MANAGE_PERMISSIONS = "MANAGE_PERMISSIONS"
    VIEW_AUDIT_LOG = "VIEW_AUDIT_LOG"

    # Wallet / transfer (Phase I.3 — declared here so the RBAC plumbing
    # is in place before the wallet endpoints land).
    CREATE_WALLET = "CREATE_WALLET"
    TRANSFER = "TRANSFER"
    MINT = "MINT"
    FREEZE_WALLET = "FREEZE_WALLET"
    UNFREEZE_WALLET = "UNFREEZE_WALLET"
    VIEW_WALLETS = "VIEW_WALLETS"
    VIEW_TRANSFERS = "VIEW_TRANSFERS"
    CREATE_CURRENCY = "CREATE_CURRENCY"
    CREATE_TREASURY_WALLET = "CREATE_TREASURY_WALLET"
    MANAGE_EXCHANGE_RATES = "MANAGE_EXCHANGE_RATES"

    # KYC admin review (Phase 6g-admin)
    REVIEW_KYC = "REVIEW_KYC"

    # Treasury dual-sign envelope (Phase 7.8 — BR-TR-*)
    INITIATE_TREASURY_DISTRIBUTION = "INITIATE_TREASURY_DISTRIBUTION"
    APPROVE_TREASURY_DISTRIBUTION = "APPROVE_TREASURY_DISTRIBUTION"
    VIEW_TREASURY_DISTRIBUTIONS = "VIEW_TREASURY_DISTRIBUTIONS"
    APPROVE_TREASURY_MINT_OP = "APPROVE_TREASURY_MINT_OP"
    VIEW_TREASURY_MINT_OPS = "VIEW_TREASURY_MINT_OPS"


# Role baselines — least-privilege by default.
#
# ADMIN owns user/role/permission management, wallet management ops
# (VIEW_WALLETS, FREEZE_WALLET, UNFREEZE_WALLET), and their own wallet
# ops. MINT and VIEW_TRANSFERS remain absent from the baseline: MINT
# modifies the token supply and VIEW_TRANSFERS exposes all financial
# history — both require an explicit per-admin grant via
# `POST /api/v1/admin/users/<self>/permissions` which leaves an audit_log
# row for every elevation.
#
# VIEW_WALLETS / FREEZE_WALLET / UNFREEZE_WALLET are included because they
# are management operations (oversight, remediation) rather than supply-
# mutating actions; the new admin wallet endpoints require them.
#
# OPERATOR is "audit-light": own wallet ops + cross-user read of wallets
# and transfers. Useful for compliance / monitoring roles that need to
# inspect activity without being able to manage users or mint coin.
#
# VIEWER is the default role assigned at registration: own wallet
# operations only, no cross-user visibility, no admin surface.
#
# `MANAGE_PERMISSIONS` lives in ADMIN's baseline so the role can
# self-elevate when needed; the action is audited via the `audit_log`
# table written by `/api/v1/admin/users/<id>/permissions`.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.ADMIN.value: {
        # User & role management
        Permission.CREATE_USER.value,
        Permission.VIEW_USERS.value,
        Permission.UPDATE_USER.value,
        Permission.BAN_USER.value,
        Permission.UNBAN_USER.value,
        Permission.DELETE_USER.value,
        Permission.RESTORE_USER.value,
        Permission.ASSIGN_ROLE.value,
        Permission.MANAGE_PERMISSIONS.value,
        Permission.VIEW_AUDIT_LOG.value,
        # Wallet management (oversight / remediation — not supply-mutating).
        Permission.VIEW_WALLETS.value,
        Permission.FREEZE_WALLET.value,
        Permission.UNFREEZE_WALLET.value,
        # Own wallet ops — admins are users too.
        Permission.CREATE_WALLET.value,
        Permission.TRANSFER.value,
        # Multi-currency admin ops (MC-1..MC-3).
        Permission.CREATE_CURRENCY.value,
        Permission.CREATE_TREASURY_WALLET.value,
        Permission.MANAGE_EXCHANGE_RATES.value,
        # KYC admin review (Phase 6g-admin).
        Permission.REVIEW_KYC.value,
        # Treasury dual-sign envelope (Phase 7.8 — BR-TR-*).
        # ADMIN holds initiate / approve / view for distributions
        # (no supply impact) and view for mint ops. The existing
        # MINT permission and APPROVE_TREASURY_MINT_OP are both
        # absent — see the note below (BR-TR-11 / BR-WL-07).
        Permission.INITIATE_TREASURY_DISTRIBUTION.value,
        Permission.APPROVE_TREASURY_DISTRIBUTION.value,
        Permission.VIEW_TREASURY_DISTRIBUTIONS.value,
        Permission.VIEW_TREASURY_MINT_OPS.value,
        # NOTE: MINT, VIEW_TRANSFERS and APPROVE_TREASURY_MINT_OP
        # are deliberately absent. They each unlock a path that
        # modifies token supply (MINT directly; APPROVE_TREASURY_MINT_OP
        # via the dual-sign envelope) or exposes the full financial
        # history (VIEW_TRANSFERS). Grant them per-admin via
        # `user_permissions` so every elevation lands in `audit_log`
        # (BR-TR-11 / BR-WL-07).
    },
    Role.OPERATOR.value: {
        Permission.CREATE_WALLET.value,
        Permission.TRANSFER.value,
        Permission.VIEW_WALLETS.value,
        Permission.VIEW_TRANSFERS.value,
        # Read-only treasury surfaces (BR-TR-10) — OPERATOR can monitor
        # but neither initiate nor approve.
        Permission.VIEW_TREASURY_DISTRIBUTIONS.value,
        Permission.VIEW_TREASURY_MINT_OPS.value,
    },
    Role.VIEWER.value: {
        Permission.CREATE_WALLET.value,
        Permission.TRANSFER.value,
    },
}


def has_permission(
    *,
    user_id: str,
    roles: list[str],
    permission: str,
    role_overrides: dict[str, set[str]] | None = None,
    user_overrides: dict[str, set[str]] | None = None,
) -> bool:
    """Resolve whether `user_id` (carrying `roles`) holds `permission`.

    `role_overrides` maps a role to its full set of permissions when the
    DB has stamped overrides for that role; in its absence the baseline
    in `ROLE_PERMISSIONS` is used. `user_overrides` maps a `user_id` to
    a set of directly granted permissions and takes precedence over
    everything else.

    Both override dicts are optional so callers that operate without DB
    access (unit tests, in-memory mode) can still resolve permissions
    against the hardcoded defaults alone.
    """
    if user_overrides and user_id in user_overrides and permission in user_overrides[user_id]:
        return True

    for role in roles:
        if role_overrides and role in role_overrides:
            if permission in role_overrides[role]:
                return True
            # When a role override exists, it REPLACES the default for
            # that role — explicit row-based control is the whole point
            # of the override table. Continue to the next role rather
            # than silently falling back to the baseline.
            continue
        baseline = ROLE_PERMISSIONS.get(role, set())
        if permission in baseline:
            return True

    return False


def effective_permissions(
    *,
    role: str,
    role_overrides: dict[str, set[str]] | None = None,
) -> set[str]:
    """Return the full set of permissions a single role currently grants
    (override-aware). Useful for an admin endpoint that lists what each
    role can do."""
    if role_overrides and role in role_overrides:
        return set(role_overrides[role])
    return set(ROLE_PERMISSIONS.get(role, set()))
