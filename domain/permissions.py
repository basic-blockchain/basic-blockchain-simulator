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


# Role baselines. ADMIN gets everything. OPERATOR can run normal wallet
# operations on its own account but cannot manage other users.
# VIEWER is read-only across the surface, plus the basic wallet ops a
# regular user needs to use the simulator.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.ADMIN.value: {p.value for p in Permission},
    Role.OPERATOR.value: {
        Permission.VIEW_USERS.value,
        Permission.UPDATE_USER.value,
        Permission.CREATE_WALLET.value,
        Permission.TRANSFER.value,
        Permission.VIEW_WALLETS.value,
        Permission.VIEW_TRANSFERS.value,
    },
    Role.VIEWER.value: {
        Permission.VIEW_USERS.value,
        Permission.UPDATE_USER.value,
        Permission.CREATE_WALLET.value,
        Permission.TRANSFER.value,
        Permission.VIEW_WALLETS.value,
        Permission.VIEW_TRANSFERS.value,
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
