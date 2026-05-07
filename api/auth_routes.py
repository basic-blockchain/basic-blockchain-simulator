"""Auth endpoints (Phase I.1): register, activate, login, me.

The routes are exposed as a Quart `Blueprint` so the app factory in
`basic-blockchain.py` can mount them under the existing `/api/v1` prefix
alongside the chain/wallet/admin blueprints (those land in I.3 / I.2).

Bootstrap-admin behaviour: the very first registered user whose username
matches the `BOOTSTRAP_ADMIN_USERNAME` env var is auto-promoted to
ADMIN. Every other user starts as VIEWER (the project default). The
admin can then grant additional roles via Phase I.2 endpoints.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from domain.auth import (
    DEFAULT_ROLE,
    Role,
    create_jwt,
    generate_activation_code,
    generate_user_id,
    hash_password,
    verify_password,
)
from domain.user_repository import (
    EmailTakenError,
    UserRepositoryProtocol,
    UsernameTakenError,
)


def build_auth_blueprint(
    *,
    users: UserRepositoryProtocol,
    jwt_secret: str,
    jwt_algorithm: str = "HS256",
    jwt_ttl_seconds: int = 1800,
    bcrypt_rounds: int = 12,
    bootstrap_admin_username: str | None = None,
) -> Blueprint:
    """Create the auth blueprint bound to a specific user repository and
    JWT/bcrypt configuration. Called once from `create_app`.
    """
    bp = Blueprint("auth", __name__, url_prefix="/auth")

    # ── POST /auth/register ──────────────────────────────────────────

    @bp.route("/register", methods=["POST"])
    async def register():
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        username = (data.get("username") or "").strip()
        display_name = (data.get("display_name") or username).strip()
        email_raw = data.get("email")
        email = email_raw.strip() if isinstance(email_raw, str) and email_raw.strip() else None

        if not username or len(username) > 64:
            return bad_request("Username is required (1..64 chars)", "VALIDATION_ERROR")
        if len(display_name) > 255:
            return bad_request("Display name must be <= 255 chars", "VALIDATION_ERROR")
        if email and len(email) > 255:
            return bad_request("Email must be <= 255 chars", "VALIDATION_ERROR")

        user_id = generate_user_id()
        try:
            users.create_user(
                user_id=user_id,
                username=username,
                display_name=display_name,
                email=email,
            )
        except UsernameTakenError:
            return bad_request("Username already taken", "USERNAME_TAKEN")
        except EmailTakenError:
            return bad_request("Email already in use", "EMAIL_TAKEN")

        activation_code = generate_activation_code()
        users.create_credentials(
            user_id=user_id,
            password_hash="",
            activation_code=activation_code,
            must_change_password=False,
        )

        # Bootstrap-admin promotion runs only on a fresh database where
        # this is the first registered user — the env var alone is not
        # enough, otherwise an attacker who learns the magic username
        # would get ADMIN on every subsequent register.
        if (
            bootstrap_admin_username
            and username == bootstrap_admin_username
            and users.count_users() == 1
        ):
            users.assign_role(user_id=user_id, role=Role.ADMIN.value)
        else:
            users.assign_role(user_id=user_id, role=DEFAULT_ROLE.value)

        return (
            jsonify(
                {
                    "message": "User registered. Use the activation code to set your password.",
                    "user_id": user_id,
                    "username": username,
                    "activation_code": activation_code,
                }
            ),
            201,
        )

    # ── POST /auth/activate ──────────────────────────────────────────

    @bp.route("/activate", methods=["POST"])
    async def activate():
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        username = (data.get("username") or "").strip()
        activation_code = (data.get("activation_code") or "").strip()
        password = data.get("password")
        if not username or not activation_code or not isinstance(password, str):
            return bad_request(
                "username, activation_code and password are required",
                "VALIDATION_ERROR",
            )
        if len(password) < 8:
            return bad_request("Password must be at least 8 characters", "VALIDATION_ERROR")

        user = users.get_user_by_username(username)
        if user is None:
            return bad_request("Invalid activation request", "AUTH_INVALID_ACTIVATION")
        cred = users.get_credentials(user.user_id)
        if cred is None or cred.activation_code is None or cred.activation_code != activation_code:
            return bad_request("Invalid activation request", "AUTH_INVALID_ACTIVATION")

        users.activate_credentials(
            user_id=user.user_id,
            password_hash=hash_password(password, rounds=bcrypt_rounds),
        )
        return jsonify({"message": "Account activated. You can now log in.", "user_id": user.user_id}), 200

    # ── POST /auth/login ─────────────────────────────────────────────

    @bp.route("/login", methods=["POST"])
    async def login():
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        username = (data.get("username") or "").strip()
        password = data.get("password")
        if not username or not isinstance(password, str):
            return bad_request("username and password are required", "VALIDATION_ERROR")

        user = users.get_user_by_username(username)
        cred = users.get_credentials(user.user_id) if user else None
        if user is None or cred is None or not cred.password_hash:
            # Same response for missing user / not-yet-activated / wrong
            # password so the endpoint cannot be used to enumerate
            # accounts.
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")
        if cred.activation_code is not None:
            return bad_request("Account not activated", "AUTH_NOT_ACTIVATED")
        if not verify_password(password, cred.password_hash):
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")

        roles = users.get_roles(user.user_id)
        token = create_jwt(
            user.user_id,
            roles,
            jwt_secret,
            algorithm=jwt_algorithm,
            ttl_seconds=jwt_ttl_seconds,
        )
        return (
            jsonify(
                {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": jwt_ttl_seconds,
                    "user_id": user.user_id,
                    "username": user.username,
                    "roles": roles,
                }
            ),
            200,
        )

    # ── GET /auth/me ─────────────────────────────────────────────────

    @bp.route("/me", methods=["GET"])
    async def me():
        current = require_auth()
        user = users.get_user_by_id(current.user_id)
        if user is None:
            # Token references a deleted user — treat as 401 since the
            # identity is no longer valid.
            return bad_request("User not found", "AUTH_USER_NOT_FOUND")
        return (
            jsonify(
                {
                    "user_id": user.user_id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "email": user.email,
                    "roles": users.get_roles(user.user_id),
                }
            ),
            200,
        )

    return bp


def parse_optional_str(payload: Any, key: str) -> str | None:
    """Tiny helper kept here so the routes module is self-contained."""
    value = payload.get(key) if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None
