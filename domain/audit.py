"""Audit log writer (Phase I.2).

The persistence layer (`infrastructure/postgres_user_store.py` plus its
in-memory counterpart) implements `append_audit(...)` and
`recent_audit(...)`. This module defines the action-name constants and
a thin record dataclass so the HTTP layer never invents action strings
ad-hoc — every admin action that mutates state goes through one of the
constants below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# Action names. Every entry written to `audit_log.action` must be one of
# these so consumers (operators, dashboards) can rely on a stable set.

ACTION_USER_REGISTERED: Final[str] = "USER_REGISTERED"
ACTION_USER_ACTIVATED: Final[str] = "USER_ACTIVATED"
ACTION_ROLE_GRANTED: Final[str] = "ROLE_GRANTED"
ACTION_ROLE_REVOKED: Final[str] = "ROLE_REVOKED"
ACTION_USER_BANNED: Final[str] = "USER_BANNED"
ACTION_USER_UNBANNED: Final[str] = "USER_UNBANNED"
ACTION_PERMISSION_GRANTED: Final[str] = "PERMISSION_GRANTED"
ACTION_PERMISSION_REVOKED: Final[str] = "PERMISSION_REVOKED"
ACTION_ROLE_PERMISSION_GRANTED: Final[str] = "ROLE_PERMISSION_GRANTED"
ACTION_ROLE_PERMISSION_REVOKED: Final[str] = "ROLE_PERMISSION_REVOKED"
ACTION_USER_UPDATED: Final[str] = "USER_UPDATED"
ACTION_USER_SELF_UPDATED: Final[str] = "USER_SELF_UPDATED"
ACTION_USER_DELETED: Final[str] = "USER_DELETED"
ACTION_USER_RESTORED: Final[str] = "USER_RESTORED"
ACTION_WALLET_FROZEN: Final[str] = "WALLET_FROZEN"
ACTION_WALLET_UNFROZEN: Final[str] = "WALLET_UNFROZEN"
ACTION_TEMP_PASSWORD_ISSUED: Final[str] = "TEMP_PASSWORD_ISSUED"
ACTION_PASSWORD_CHANGED: Final[str] = "PASSWORD_CHANGED"
ACTION_CURRENCY_CREATED: Final[str] = "CURRENCY_CREATED"
ACTION_TREASURY_WALLET_CREATED: Final[str] = "TREASURY_WALLET_CREATED"
ACTION_EXCHANGE_RATE_SET: Final[str] = "EXCHANGE_RATE_SET"


@dataclass(slots=True)
class AuditEntry:
    """Read-side record returned by `recent_audit`."""

    id: int
    actor_id: str
    action: str
    target_id: str | None
    details: dict[str, object]
    created_at: str
