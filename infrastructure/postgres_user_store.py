"""PostgreSQL adapter for the user repository (Phase I.1).

Maps the contract in `domain/user_repository.py` onto the `users`,
`user_credentials`, and `user_roles` tables introduced by V007 / V008.

Each method opens a fresh connection to keep the surface compatible with
the existing repos in this package (`PostgresBlockRepository`, etc.).
"""

from __future__ import annotations

import psycopg2
from psycopg2 import errorcodes

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
            cur.execute(
                "SELECT user_id, username, display_name, email FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        return _row_to_user(row)

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, username, display_name, email FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        return _row_to_user(row)

    def count_users(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            row = cur.fetchone()
        return int(row[0]) if row else 0

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

    def get_roles(self, user_id: str) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM user_roles WHERE user_id = %s ORDER BY granted_at",
                (user_id,),
            )
            return [row[0] for row in cur.fetchall()]


def _row_to_user(row: tuple | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        user_id=row[0],
        username=row[1],
        display_name=row[2],
        email=row[3],
    )
