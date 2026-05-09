"""User persistence contract + an in-memory implementation for tests.

The real PostgreSQL implementation lives in
`infrastructure/postgres_user_store.py`. The HTTP layer talks to whichever
adapter the app factory injects; tests can use `InMemoryUserStore` to run
without a database.

Phase I.2 extends the surface to cover RBAC overrides
(`get_role_overrides`, `grant_*` / `revoke_*` permission calls), the
`banned` flag on users, and the audit log (`append_audit`,
`recent_audit`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from domain.audit import AuditEntry


@dataclass(slots=True)
class UserRecord:
    user_id: str
    username: str
    display_name: str
    email: str | None = None
    banned: bool = False
    deleted_at: str | None = None


@dataclass(slots=True)
class CredentialsRecord:
    user_id: str
    password_hash: str
    activation_code: str | None
    activated_at: str | None
    must_change_password: bool = False


class UsernameTakenError(Exception):
    """Raised by `create_user` when the username is already used."""


class EmailTakenError(Exception):
    """Raised by `create_user` when the email is already used."""


class UserRepositoryProtocol(Protocol):
    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        email: str | None,
    ) -> None: ...

    def get_user_by_id(self, user_id: str) -> UserRecord | None: ...

    def get_user_by_username(self, username: str) -> UserRecord | None: ...

    def create_credentials(
        self,
        *,
        user_id: str,
        password_hash: str,
        activation_code: str | None,
        must_change_password: bool = False,
    ) -> None: ...

    def get_credentials(self, user_id: str) -> CredentialsRecord | None: ...

    def activate_credentials(self, *, user_id: str, password_hash: str) -> None: ...

    def assign_role(self, *, user_id: str, role: str) -> None: ...

    def revoke_role(self, *, user_id: str, role: str) -> None: ...

    def get_roles(self, user_id: str) -> list[str]: ...

    def count_users(self) -> int: ...

    def list_users(self) -> list[UserRecord]: ...

    def set_banned(self, *, user_id: str, banned: bool) -> None: ...

    # Soft-delete + profile edit (Phase I.5)
    def soft_delete_user(self, user_id: str) -> None: ...

    def restore_user(self, user_id: str) -> None: ...

    def update_user(
        self,
        *,
        user_id: str,
        display_name: str | None,
        email: str | None,
        username: str | None = None,
    ) -> None: ...

    # Permission overrides (Phase I.2)
    def get_role_overrides(self) -> dict[str, set[str]]: ...

    def get_user_overrides(self, user_id: str) -> set[str]: ...

    def grant_user_permission(self, *, user_id: str, permission: str) -> None: ...

    def revoke_user_permission(self, *, user_id: str, permission: str) -> None: ...

    # Audit log (Phase I.2)
    def append_audit(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        details: dict[str, object] | None = None,
    ) -> None: ...

    def recent_audit(self, limit: int = 50) -> list[AuditEntry]: ...


class InMemoryUserStore:
    """Memory-backed user store. Same surface as the Postgres adapter; used
    by unit tests so the auth flow can be exercised without spinning up a
    database."""

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._by_username: dict[str, str] = {}
        self._by_email: dict[str, str] = {}
        self._creds: dict[str, CredentialsRecord] = {}
        self._roles: dict[str, list[str]] = {}
        # Phase I.2 — RBAC overrides + audit log.
        self._role_overrides: dict[str, set[str]] = {}
        self._user_overrides: dict[str, set[str]] = {}
        self._audit: list[AuditEntry] = []
        self._audit_seq = 0

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        email: str | None,
    ) -> None:
        if username in self._by_username:
            raise UsernameTakenError(username)
        if email and email in self._by_email:
            raise EmailTakenError(email)
        self._users[user_id] = UserRecord(
            user_id=user_id, username=username, display_name=display_name, email=email
        )
        self._by_username[username] = user_id
        if email:
            self._by_email[email] = user_id

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> UserRecord | None:
        uid = self._by_username.get(username)
        return self._users.get(uid) if uid else None

    def create_credentials(
        self,
        *,
        user_id: str,
        password_hash: str,
        activation_code: str | None,
        must_change_password: bool = False,
    ) -> None:
        self._creds[user_id] = CredentialsRecord(
            user_id=user_id,
            password_hash=password_hash,
            activation_code=activation_code,
            activated_at=None,
            must_change_password=must_change_password,
        )

    def get_credentials(self, user_id: str) -> CredentialsRecord | None:
        return self._creds.get(user_id)

    def activate_credentials(self, *, user_id: str, password_hash: str) -> None:
        cred = self._creds.get(user_id)
        if cred is None:
            raise KeyError(user_id)
        # Replace fields on the immutable-ish dataclass.
        self._creds[user_id] = CredentialsRecord(
            user_id=user_id,
            password_hash=password_hash,
            activation_code=None,
            activated_at="now",  # InMemory uses sentinel; PG uses now()
            must_change_password=False,
        )

    def assign_role(self, *, user_id: str, role: str) -> None:
        self._roles.setdefault(user_id, [])
        if role not in self._roles[user_id]:
            self._roles[user_id].append(role)

    def revoke_role(self, *, user_id: str, role: str) -> None:
        if user_id in self._roles and role in self._roles[user_id]:
            self._roles[user_id].remove(role)

    def get_roles(self, user_id: str) -> list[str]:
        return list(self._roles.get(user_id, []))

    def count_users(self) -> int:
        return len(self._users)

    def list_users(self) -> list[UserRecord]:
        return [self._users[uid] for uid in sorted(self._users)]

    def set_banned(self, *, user_id: str, banned: bool) -> None:
        rec = self._users.get(user_id)
        if rec is None:
            raise KeyError(user_id)
        self._users[user_id] = UserRecord(
            user_id=rec.user_id,
            username=rec.username,
            display_name=rec.display_name,
            email=rec.email,
            banned=banned,
            deleted_at=rec.deleted_at,
        )

    # ── Soft-delete + profile edit (Phase I.5) ─────────────────────

    def soft_delete_user(self, user_id: str) -> None:
        rec = self._users.get(user_id)
        if rec is None:
            raise KeyError(user_id)
        self._users[user_id] = UserRecord(
            user_id=rec.user_id,
            username=rec.username,
            display_name=rec.display_name,
            email=rec.email,
            banned=rec.banned,
            deleted_at="deleted",
        )

    def restore_user(self, user_id: str) -> None:
        rec = self._users.get(user_id)
        if rec is None:
            raise KeyError(user_id)
        self._users[user_id] = UserRecord(
            user_id=rec.user_id,
            username=rec.username,
            display_name=rec.display_name,
            email=rec.email,
            banned=rec.banned,
            deleted_at=None,
        )

    def update_user(
        self,
        *,
        user_id: str,
        display_name: str | None,
        email: str | None,
        username: str | None = None,
    ) -> None:
        rec = self._users.get(user_id)
        if rec is None:
            raise KeyError(user_id)
        # Email index — keep in sync so future username/email lookups
        # remain consistent after profile edits. Mirrors the UNIQUE
        # constraint on `users.email` in PostgreSQL so both adapters
        # raise the same domain error.
        new_email = rec.email if email is None else email
        if email is not None and email != rec.email:
            if email and email in self._by_email:
                raise EmailTakenError(email)
            if rec.email and rec.email in self._by_email:
                del self._by_email[rec.email]
            if email:
                self._by_email[email] = user_id
        # Username index — Gap #6: self-service profile updates allow
        # users to rename themselves. Reject conflicts up-front so the
        # contract matches `create_user`.
        new_username = rec.username if username is None else username
        if username is not None and username != rec.username:
            if username in self._by_username:
                raise UsernameTakenError(username)
            del self._by_username[rec.username]
            self._by_username[username] = user_id
        self._users[user_id] = UserRecord(
            user_id=rec.user_id,
            username=new_username,
            display_name=display_name if display_name is not None else rec.display_name,
            email=new_email,
            banned=rec.banned,
            deleted_at=rec.deleted_at,
        )

    # ── Permission overrides ───────────────────────────────────────

    def get_role_overrides(self) -> dict[str, set[str]]:
        return {role: set(perms) for role, perms in self._role_overrides.items()}

    def get_user_overrides(self, user_id: str) -> set[str]:
        return set(self._user_overrides.get(user_id, set()))

    def grant_user_permission(self, *, user_id: str, permission: str) -> None:
        self._user_overrides.setdefault(user_id, set()).add(permission)

    def revoke_user_permission(self, *, user_id: str, permission: str) -> None:
        if user_id in self._user_overrides:
            self._user_overrides[user_id].discard(permission)

    # ── Audit log ──────────────────────────────────────────────────

    def append_audit(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        details: dict[str, object] | None = None,
    ) -> None:
        self._audit_seq += 1
        self._audit.append(
            AuditEntry(
                id=self._audit_seq,
                actor_id=actor_id,
                action=action,
                target_id=target_id,
                details=dict(details or {}),
                created_at="now",
            )
        )

    def recent_audit(self, limit: int = 50) -> list[AuditEntry]:
        return list(reversed(self._audit[-limit:]))
