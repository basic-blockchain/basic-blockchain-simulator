"""Authentication primitives: roles, password hashing, JWT issuance.

Pure domain module: no Quart, no psycopg2 imports. The HTTP layer (Quart
`before_request` middleware in `api/auth_middleware.py`) and the persistence
layer (`infrastructure/postgres_user_store.py`) consume these helpers but
this file is consumable from any context — including tests with no DB.

Phase I.2 will extend this with the `Permission` enum and `has_permission`
3-level lookup. Phase I.3 will add a separate `domain/crypto.py` for the
BIP-39 mnemonic and secp256k1 signing — that is wallet-side cryptography
and intentionally lives next to wallet code, not here.
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from enum import Enum

import bcrypt
import jwt


# ── Roles ────────────────────────────────────────────────────────────────


class Role(str, Enum):
    """User role labels. Mirrors the `user_role` enum in V008."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


DEFAULT_ROLE: Role = Role.VIEWER
"""Role assigned to a newly registered user that does not match
`BOOTSTRAP_ADMIN_USERNAME`. VIEWER is the safest default; ADMIN can
upgrade specific users via Phase I.2 endpoints."""


# ── Password hashing ─────────────────────────────────────────────────────


def hash_password(plain: str, rounds: int = 12) -> str:
    """Return a bcrypt hash for `plain` using the given cost factor.

    `rounds` is the bcrypt cost (4..31). 12 is the project default and
    matches the reference repo. Higher costs are safer but slower; tests
    can pass `rounds=4` to keep the suite fast.
    """
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash. Returns False
    on any failure (including a malformed hash) — never raises."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── Activation codes ─────────────────────────────────────────────────────


_ACTIVATION_ALPHABET = string.ascii_uppercase + string.digits


def generate_activation_code(length: int = 16) -> str:
    """One-shot code emitted at registration. The user exchanges it for a
    chosen password in `POST /api/v1/auth/activate`. Cryptographically
    secure; uppercase letters + digits for easy copy/paste."""
    return "".join(secrets.choice(_ACTIVATION_ALPHABET) for _ in range(length))


# ── User identifier ──────────────────────────────────────────────────────


def generate_user_id() -> str:
    """Server-generated opaque ID stored as `users.user_id` and used as the
    JWT `sub` claim. Hex-encoded 16-byte token: 32 chars, plenty of entropy,
    no risk of collision."""
    return secrets.token_hex(16)


# ── JWT ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JWTPayload:
    """Decoded JWT body. Construct via `decode_jwt`; never instantiate
    directly from request input."""

    sub: str
    roles: list[str]
    iat: int
    exp: int


class AuthError(Exception):
    """Raised by `decode_jwt` for any token-validation failure (invalid
    signature, expired, malformed). The HTTP layer maps this to 401."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def create_jwt(
    user_id: str,
    roles: list[str],
    secret: str,
    *,
    algorithm: str = "HS256",
    ttl_seconds: int = 1800,
) -> str:
    """Issue an HS256 JWT carrying user identity and roles.

    The payload follows the canonical claims (`sub`, `iat`, `exp`) plus a
    `roles` list. `secret` must be configured via the `JWT_SECRET` env var;
    `ttl_seconds` defaults to 30 minutes.
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "roles": list(roles),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_jwt(token: str, secret: str, *, algorithm: str = "HS256") -> JWTPayload:
    """Decode and validate a JWT. Raises `AuthError` on any failure.

    The error code is one of:
      - AUTH_EXPIRED_TOKEN — `exp` is in the past
      - AUTH_INVALID_TOKEN — signature, structure, or claims invalid
    """
    try:
        decoded = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired", "AUTH_EXPIRED_TOKEN") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid token", "AUTH_INVALID_TOKEN") from exc

    sub = decoded.get("sub")
    roles = decoded.get("roles")
    iat = decoded.get("iat")
    exp = decoded.get("exp")
    if not isinstance(sub, str) or not isinstance(roles, list) or not isinstance(iat, int) or not isinstance(exp, int):
        raise AuthError("Invalid token payload", "AUTH_INVALID_TOKEN")
    return JWTPayload(sub=sub, roles=[str(r) for r in roles], iat=iat, exp=exp)
