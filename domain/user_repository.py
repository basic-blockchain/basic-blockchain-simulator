"""User persistence contract + an in-memory implementation for tests.

The real PostgreSQL implementation lives in
`infrastructure/postgres_user_store.py`. The HTTP layer talks to whichever
adapter the app factory injects; tests can use `InMemoryUserStore` to run
without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class UserRecord:
    user_id: str
    username: str
    display_name: str
    email: str | None = None


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

    def get_roles(self, user_id: str) -> list[str]: ...

    def count_users(self) -> int: ...


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

    def get_roles(self, user_id: str) -> list[str]:
        return list(self._roles.get(user_id, []))

    def count_users(self) -> int:
        return len(self._users)
