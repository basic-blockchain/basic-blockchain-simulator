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
from psycopg2 import sql
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
        # Sticky flag set on first UndefinedColumn from the extended
        # users SELECT — keeps us from re-attempting the wide query on
        # every call once we know the schema is pre-V018/V019. Reset by
        # restarting the process after running migrations.
        self._users_legacy_only = False

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def _select_users(self, where_clause: str, params: tuple) -> list[tuple]:
        """Execute a users SELECT resilient to pre-V018/V019 schemas.

        Tries the extended column set first (country, kyc_level,
        last_active, created_at + V019 kyc_documents/pending/submitted);
        on `UndefinedColumn` it falls back to the legacy projection so
        environments that have not yet run migrate.py keep working with
        a degraded but valid UserRecord (KYC fields default to L0 /
        missing). The fallback is sticky for the life of this store
        instance so we do not pay the failure round-trip every call.
        """
        if self._users_legacy_only:
            query = _USER_SELECT_LEGACY + where_clause
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()

        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_USER_SELECT + where_clause, params)
                return cur.fetchall()
        except psycopg2.errors.UndefinedColumn:
            # Schema predates V018 (and possibly V019). Latch onto the
            # legacy projection until the process restarts; the caller
            # gets a UserRecord whose new fields default sensibly.
            self._users_legacy_only = True
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_USER_SELECT_LEGACY + where_clause, params)
                return cur.fetchall()

    # ── Users ─────────────────────────────────────────────────────────

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        email: str | None,
        country: str | None = None,
    ) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_id, username, display_name, email, country) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, username, display_name, email, country),
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
        rows = self._select_users(" WHERE user_id = %s", (user_id,))
        return _row_to_user(rows[0]) if rows else None

    def get_user_by_username(self, username: str) -> UserRecord | None:
        rows = self._select_users(" WHERE username = %s", (username,))
        return _row_to_user(rows[0]) if rows else None

    def count_users(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_users(self) -> list[UserRecord]:
        rows = self._select_users(" ORDER BY username", ())
        return [_row_to_user(row) for row in rows if _row_to_user(row) is not None]  # type: ignore[misc]

    def set_banned(self, *, user_id: str, banned: bool) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET banned = %s, updated_at = now() WHERE user_id = %s",
                (banned, user_id),
            )

    def touch_last_active(self, *, user_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_active = now() WHERE user_id = %s",
                (user_id,),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

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
        username: str | None = None,
    ) -> None:
        # Build SET clause dynamically so callers can update any subset
        # of (username, display_name, email) in isolation. `updated_at =
        # now()` is always touched so the row carries an audit trail of
        # the most recent edit (admin or self-service — Gap #6).
        sets: list[str] = []
        params: list[object] = []
        if username is not None:
            sets.append("username = %s")
            params.append(username)
        if display_name is not None:
            sets.append("display_name = %s")
            params.append(display_name)
        if email is not None:
            sets.append("email = %s")
            params.append(email)
        if not sets:
            return
        params.append(user_id)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                assignments = [sql.SQL(part) for part in sets]
                cur.execute(
                    sql.SQL("UPDATE users SET {}, updated_at = now() WHERE user_id = %s").format(
                        sql.SQL(", ").join(assignments)
                    ),
                    params,
                )
                if cur.rowcount == 0:
                    raise KeyError(user_id)
        except psycopg2.errors.UniqueViolation as exc:
            constraint = (exc.diag.constraint_name or "").lower()
            if "username" in constraint:
                raise UsernameTakenError(username or "") from exc
            if "email" in constraint:
                raise EmailTakenError(email or "") from exc
            raise

    # ── KYC (Phase 6g) ────────────────────────────────────────────

    def set_kyc_documents(
        self, *, user_id: str, documents: dict[str, dict[str, object]]
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET kyc_documents = %s, updated_at = now() "
                "WHERE user_id = %s",
                (Json(documents), user_id),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

    def set_kyc_pending_review(
        self, *, user_id: str, target: str | None, submitted_at: str | None
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET kyc_pending_review = %s, kyc_submitted_at = %s, "
                "updated_at = now() WHERE user_id = %s",
                (target, submitted_at, user_id),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

    def set_kyc_level(self, *, user_id: str, level: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET kyc_level = %s, updated_at = now() WHERE user_id = %s",
                (level, user_id),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

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

    def set_password(
        self,
        *,
        user_id: str,
        password_hash: str,
        must_change_password: bool = False,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE user_credentials "
                "SET password_hash = %s, must_change_password = %s, "
                "    activation_code = NULL, updated_at = now() "
                "WHERE user_id = %s",
                (password_hash, must_change_password, user_id),
            )
            if cur.rowcount == 0:
                raise KeyError(user_id)

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
        conditions: list[sql.SQL] = []
        params: list[object] = []
        if action:
            conditions.append(sql.SQL("action = %s"))
            params.append(action)
        if actor_id:
            conditions.append(sql.SQL("actor_id = %s"))
            params.append(actor_id)
        if target_id:
            conditions.append(sql.SQL("target_id = %s"))
            params.append(target_id)
        where = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
            if conditions
            else sql.SQL("")
        )
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT id, actor_id, action, target_id, details, created_at "
                    "FROM audit_log"
                )
                + where
                + sql.SQL(" ORDER BY id DESC LIMIT %s"),
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
    "SELECT user_id, username, display_name, email, banned, deleted_at, "
    "country, kyc_level, last_active, created_at, "
    "kyc_documents, kyc_pending_review, kyc_submitted_at FROM users"
)

# Pre-V018 column set. Used by `_select_users` as a fallback when the
# extended SELECT raises UndefinedColumn on databases that have not yet
# run the migrations. `_row_to_user` already tolerates short rows.
_USER_SELECT_LEGACY = (
    "SELECT user_id, username, display_name, email, banned, deleted_at "
    "FROM users"
)


def _row_to_user(row: tuple | None) -> UserRecord | None:
    if row is None:
        return None
    raw_docs = row[10] if len(row) > 10 and row[10] is not None else {}
    if isinstance(raw_docs, str):
        try:
            raw_docs = json.loads(raw_docs)
        except json.JSONDecodeError:
            raw_docs = {}
    return UserRecord(
        user_id=row[0],
        username=row[1],
        display_name=row[2],
        email=row[3],
        banned=bool(row[4]) if len(row) > 4 else False,
        deleted_at=str(row[5]) if len(row) > 5 and row[5] is not None else None,
        country=row[6] if len(row) > 6 and row[6] is not None else None,
        kyc_level=str(row[7]) if len(row) > 7 and row[7] is not None else "L0",
        last_active=str(row[8]) if len(row) > 8 and row[8] is not None else None,
        created_at=str(row[9]) if len(row) > 9 and row[9] is not None else None,
        kyc_documents=dict(raw_docs) if isinstance(raw_docs, dict) else {},
        kyc_pending_review=str(row[11]) if len(row) > 11 and row[11] is not None else None,
        kyc_submitted_at=str(row[12]) if len(row) > 12 and row[12] is not None else None,
    )
