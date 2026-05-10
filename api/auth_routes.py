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
from domain.audit import ACTION_PASSWORD_CHANGED
from domain.audit import ACTION_USER_SELF_UPDATED
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
        user_id_field = (data.get("user_id") or "").strip()
        password = data.get("password")
        if (not username and not user_id_field) or not isinstance(password, str):
            return bad_request("username (or user_id) and password are required", "VALIDATION_ERROR")

        # Resolve identity: username takes precedence; user_id is the fallback.
        # Both paths return the same opaque error so the endpoint cannot be
        # used to enumerate valid identifiers. (BR-AU-03 + BR-RB-04.)
        if username:
            user = users.get_user_by_username(username)
        else:
            user = users.get_user_by_id(user_id_field)
        cred = users.get_credentials(user.user_id) if user else None
        if user is None or cred is None or not cred.password_hash:
            # Same response for missing user / not-yet-activated / wrong
            # password / banned, so the endpoint cannot be used to
            # enumerate accounts. (Phase I.2 BR-AU-03 + BR-RB-04.)
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")
        if cred.activation_code is not None:
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")
        if not verify_password(password, cred.password_hash):
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")
        if user.banned:
            return bad_request("Invalid credentials", "AUTH_INVALID_CREDENTIALS")

        roles = users.get_roles(user.user_id)
        token = create_jwt(
            user.user_id,
            roles,
            jwt_secret,
            algorithm=jwt_algorithm,
            ttl_seconds=jwt_ttl_seconds,
        )
        # When must_change_password is set, we still hand out a JWT — but
        # the client is expected to redirect to the change-password screen
        # before doing anything else. The flag is the signal; clients that
        # ignore it will keep working but the obligation persists in the DB.
        return (
            jsonify(
                {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": jwt_ttl_seconds,
                    "user_id": user.user_id,
                    "username": user.username,
                    "roles": roles,
                    "must_change_password": cred.must_change_password,
                }
            ),
            200,
        )

    # ── POST /auth/change-password ───────────────────────────────────

    @bp.route("/change-password", methods=["POST"])
    async def change_password():
        current = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        if not isinstance(current_password, str) or not isinstance(new_password, str):
            return bad_request(
                "current_password and new_password are required",
                "VALIDATION_ERROR",
            )
        if len(new_password) < 8:
            return bad_request(
                "New password must be at least 8 characters", "VALIDATION_ERROR"
            )

        cred = users.get_credentials(current.user_id)
        if cred is None or not verify_password(current_password, cred.password_hash):
            return bad_request(
                "Current password is incorrect", "AUTH_INVALID_CREDENTIALS"
            )

        users.set_password(
            user_id=current.user_id,
            password_hash=hash_password(new_password, rounds=bcrypt_rounds),
            must_change_password=False,
        )
        users.append_audit(
            actor_id=current.user_id,
            action=ACTION_PASSWORD_CHANGED,
            target_id=current.user_id,
            details={},
        )
        return jsonify({"message": "Password changed successfully."}), 200

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

    # ── PATCH /auth/me ───────────────────────────────────────────────
    #
    # Gap #6 — self-service profile update. Any authenticated user can
    # change their own `display_name`, `email`, or `username`. The
    # admin-side `PATCH /admin/users/<id>` remains the path for editing
    # other users; this route only ever touches `current.user_id`.

    @bp.route("/me", methods=["PATCH"])
    async def update_me():
        current = require_auth()
        user = users.get_user_by_id(current.user_id)
        if user is None:
            return bad_request("User not found", "AUTH_USER_NOT_FOUND")
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")

        display_name = data.get("display_name")
        email = data.get("email")
        username = data.get("username")

        # Per-field validation. Lengths mirror the constraints used by
        # `register` so the two endpoints stay symmetric.
        if display_name is not None and (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name) > 255
        ):
            return bad_request(
                "display_name must be a non-empty string <= 255 chars",
                "VALIDATION_ERROR",
            )
        if email is not None and (not isinstance(email, str) or len(email) > 255):
            return bad_request(
                "email must be a string <= 255 chars",
                "VALIDATION_ERROR",
            )
        if username is not None and (
            not isinstance(username, str)
            or not username.strip()
            or len(username) > 64
        ):
            return bad_request(
                "username must be a non-empty string <= 64 chars",
                "VALIDATION_ERROR",
            )

        if display_name is None and email is None and username is None:
            return bad_request(
                "At least one field must be provided",
                "VALIDATION_ERROR",
            )

        try:
            users.update_user(
                user_id=current.user_id,
                display_name=display_name.strip() if isinstance(display_name, str) else None,
                email=email.strip() if isinstance(email, str) else None,
                username=username.strip() if isinstance(username, str) else None,
            )
        except UsernameTakenError:
            return bad_request("Username already taken", "USERNAME_TAKEN")
        except EmailTakenError:
            return bad_request("Email already in use", "EMAIL_TAKEN")

        users.append_audit(
            actor_id=current.user_id,
            action=ACTION_USER_SELF_UPDATED,
            target_id=current.user_id,
            details={
                "display_name": display_name,
                "email": email,
                "username": username,
            },
        )
        updated = users.get_user_by_id(current.user_id)
        return (
            jsonify(
                {
                    "user_id": current.user_id,
                    "username": updated.username if updated else None,
                    "display_name": updated.display_name if updated else None,
                    "email": updated.email if updated else None,
                }
            ),
            200,
        )

    return bp


def parse_optional_str(payload: Any, key: str) -> str | None:
    """Tiny helper kept here so the routes module is self-contained."""
    value = payload.get(key) if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None
