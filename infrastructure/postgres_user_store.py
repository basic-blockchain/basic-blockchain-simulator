"""PostgreSQL adapter for the user repository (Phase I.1).

Maps the contract in `domain/user_repository.py` onto the `users`,
`user_credentials`, and `user_roles` tables introduced by V007 / V008.

Each method opens a fresh connection to keep the surface compatible with
the existing repos in this package (`PostgresBlockRepository`, etc.).
"""

from __future__ import annotations

import json

import psycopg2
from psycopg2 import errorcodes
from psycopg2.extras import Json

from domain.audit import AuditEntry
from domain.user_repository import (
    CredentialsRecord,
    EmailTakenError,
    UserRecord,
    UsernameTakenError,
)


class PostgresUserStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    # ── Users ─────────────────────────────────────────────────────────

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        email: str | None,
    ) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_id, username, display_name, email) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, username, display_name, email),
                )
        except psycopg2.errors.UniqueViolation as exc:
            # Translate the UNIQUE constraint name back into a domain error
            # so the HTTP layer can pick the right error code.
            constraint = (exc.diag.constraint_name or "").lower()
            if "username" in constraint:
                raise UsernameTakenError(username) from exc
            if "email" in constraint:
                raise EmailTakenError(email or "") from exc
            raise

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_USER_SELECT + " WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
        return _row_to_user(row)

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_USER_SELECT + " WHERE username = %s", (username,))
            row = cur.fetchone()
        return _row_to_user(row)

    def count_users(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_users(self) -> list[UserRecord]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_USER_SELECT + " ORDER BY username")
            rows = cur.fetchall()
        return [_row_to_user(row) for row in rows if _row_to_user(row) is not None]  # type: ignore[misc]

    def set_banned(self, *, user_id: str, banned: bool) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET banned = %s, updated_at = now() WHERE user_id = %s",
                (banned, user_id),
            )

    # ── Soft-delete + profile edit (Phase I.5) ────────────────────

    def soft_delete_user(self, user_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET deleted_at = now(), updated_at = now() "
                "WHERE user_id = %s AND deleted_at IS NULL",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

    def restore_user(self, user_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET deleted_at = NULL, updated_at = now() "
                "WHERE user_id = %s AND deleted_at IS NOT NULL",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

    def update_user(
        self,
        *,
        user_id: str,
        display_name: str | None,
        email: str | None,
    ) -> None:
        # Build SET clause dynamically so callers can update either field
        # in isolation. `updated_at = now()` is always touched so the row
        # carries an audit trail of the most recent admin edit.
        sets: list[str] = []
        params: list[object] = []
        if display_name is not None:
            sets.append("display_name = %s")
            params.append(display_name)
        if email is not None:
            sets.append("email = %s")
            params.append(email)
        if not sets:
            return
        sets.append("updated_at = now()")
        params.append(user_id)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE user_id = %s",
                    params,
                )
                if cur.rowcount == 0:
                    raise KeyError(user_id)
        except psycopg2.errors.UniqueViolation as exc:
            constraint = (exc.diag.constraint_name or "").lower()
            if "email" in constraint:
                raise EmailTakenError(email or "") from exc
            raise

    # ── Credentials ───────────────────────────────────────────────────

    def create_credentials(
        self,
        *,
        user_id: str,
        password_hash: str,
        activation_code: str | None,
        must_change_password: bool = False,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_credentials "
                "(user_id, password_hash, activation_code, must_change_password) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, password_hash, activation_code, must_change_password),
            )

    def get_credentials(self, user_id: str) -> CredentialsRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, password_hash, activation_code, activated_at, must_change_password "
                "FROM user_credentials WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return CredentialsRecord(
            user_id=row[0],
            password_hash=row[1],
            activation_code=row[2],
            activated_at=str(row[3]) if row[3] is not None else None,
            must_change_password=bool(row[4]),
        )

    def activate_credentials(self, *, user_id: str, password_hash: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE user_credentials "
                "SET password_hash = %s, "
                "    activation_code = NULL, "
                "    activated_at = now(), "
                "    must_change_password = FALSE, "
                "    updated_at = now() "
                "WHERE user_id = %s",
                (password_hash, user_id),
            )

    # ── Roles ─────────────────────────────────────────────────────────

    def assign_role(self, *, user_id: str, role: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_roles (user_id, role) VALUES (%s, %s) "
                "ON CONFLICT (user_id, role) DO NOTHING",
                (user_id, role),
            )

    def revoke_role(self, *, user_id: str, role: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_roles WHERE user_id = %s AND role = %s",
                (user_id, role),
            )

    def get_roles(self, user_id: str) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM user_roles WHERE user_id = %s ORDER BY granted_at",
                (user_id,),
            )
            return [row[0] for row in cur.fetchall()]

    # ── Permission overrides (Phase I.2) ──────────────────────────

    def get_role_overrides(self) -> dict[str, set[str]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT role, permission_id FROM role_permissions")
            overrides: dict[str, set[str]] = {}
            for role, perm in cur.fetchall():
                overrides.setdefault(role, set()).add(perm)
        return overrides

    def get_user_overrides(self, user_id: str) -> set[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT permission_id FROM user_permissions WHERE user_id = %s",
                (user_id,),
            )
            return {row[0] for row in cur.fetchall()}

    def grant_user_permission(self, *, user_id: str, permission: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_permissions (user_id, permission_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, permission),
            )

    def revoke_user_permission(self, *, user_id: str, permission: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_permissions WHERE user_id = %s AND permission_id = %s",
                (user_id, permission),
            )

    def grant_role_permission(self, *, role: str, permission: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO role_permissions (role, permission_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (role, permission),
            )

    def revoke_role_permission(self, *, role: str, permission: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM role_permissions WHERE role = %s AND permission_id = %s",
                (role, permission),
            )

    # ── Audit log (Phase I.2) ─────────────────────────────────────

    def append_audit(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str | None,
        details: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (actor_id, action, target_id, details) "
                "VALUES (%s, %s, %s, %s)",
                (actor_id, action, target_id, Json(details or {})),
            )

    def recent_audit(
        self,
        limit: int = 50,
        *,
        action: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
    ) -> list[AuditEntry]:
        conditions: list[str] = []
        params: list[object] = []
        if action:
            conditions.append("action = %s")
            params.append(action)
        if actor_id:
            conditions.append("actor_id = %s")
            params.append(actor_id)
        if target_id:
            conditions.append("target_id = %s")
            params.append(target_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, actor_id, action, target_id, details, created_at "
                f"FROM audit_log{where} ORDER BY id DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [
            AuditEntry(
                id=int(row[0]),
                actor_id=row[1],
                action=row[2],
                target_id=row[3],
                details=row[4] if isinstance(row[4], dict) else json.loads(row[4]),
                created_at=str(row[5]),
            )
            for row in rows
        ]


_USER_SELECT = (
    "SELECT user_id, username, display_name, email, banned, deleted_at FROM users"
)


def _row_to_user(row: tuple | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        user_id=row[0],
        username=row[1],
        display_name=row[2],
        email=row[3],
        banned=bool(row[4]) if len(row) > 4 else False,
        deleted_at=str(row[5]) if len(row) > 5 and row[5] is not None else None,
    )
