"""Admin endpoints (Phase I.2): list users, grant/revoke roles, ban/unban,
read recent audit entries.

Mounted under `/api/v1/admin`. Every route is gated by
`@require_permission(...)` so the matching ADMIN-default permission must
be present (or granted by override).
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from api.permissions import require_permission
from domain.audit import (
    ACTION_PERMISSION_GRANTED,
    ACTION_PERMISSION_REVOKED,
    ACTION_ROLE_GRANTED,
    ACTION_ROLE_REVOKED,
    ACTION_USER_BANNED,
    ACTION_USER_UNBANNED,
)
from domain.auth import Role
from domain.permissions import Permission
from domain.user_repository import UserRepositoryProtocol


_VALID_ROLES = {r.value for r in Role}
_VALID_PERMISSIONS = {p.value for p in Permission}


def build_admin_blueprint(*, users: UserRepositoryProtocol) -> Blueprint:
    """Return the `/admin` blueprint bound to a user repository.

    The repository is the one place we read users / roles / overrides /
    audit from, so the routes never reach into the persistence layer
    directly.
    """
    bp = Blueprint("admin", __name__, url_prefix="/admin")

    # ── GET /admin/users ─────────────────────────────────────────────

    @bp.route("/users", methods=["GET"])
    @require_permission(Permission.VIEW_USERS)
    async def list_users():
        out = []
        for record in users.list_users():
            out.append(
                {
                    "user_id": record.user_id,
                    "username": record.username,
                    "display_name": record.display_name,
                    "email": record.email,
                    "banned": record.banned,
                    "roles": users.get_roles(record.user_id),
                }
            )
        return jsonify({"users": out, "count": len(out)}), 200

    # ── POST /admin/users/<user_id>/roles ────────────────────────────

    @bp.route("/users/<user_id>/roles", methods=["POST"])
    @require_permission(Permission.ASSIGN_ROLE)
    async def manage_role(user_id: str):
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        action = (data.get("action") or "").lower()
        role = (data.get("role") or "").upper()
        if action not in {"grant", "revoke"}:
            return bad_request("action must be 'grant' or 'revoke'", "VALIDATION_ERROR")
        if role not in _VALID_ROLES:
            return bad_request(f"role must be one of {sorted(_VALID_ROLES)}", "VALIDATION_ERROR")

        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")

        if action == "grant":
            users.assign_role(user_id=user_id, role=role)
            audit_action = ACTION_ROLE_GRANTED
        else:
            users.revoke_role(user_id=user_id, role=role)
            audit_action = ACTION_ROLE_REVOKED

        users.append_audit(
            actor_id=actor.user_id,
            action=audit_action,
            target_id=user_id,
            details={"role": role},
        )
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "roles": users.get_roles(user_id),
                    "action": audit_action,
                }
            ),
            200,
        )

    # ── POST /admin/users/<user_id>/ban ──────────────────────────────

    @bp.route("/users/<user_id>/ban", methods=["POST"])
    @require_permission(Permission.BAN_USER)
    async def ban_user(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if target.user_id == actor.user_id:
            return bad_request("An admin cannot ban themselves", "SELF_ACTION_FORBIDDEN")
        users.set_banned(user_id=user_id, banned=True)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_BANNED,
            target_id=user_id,
            details={},
        )
        return jsonify({"user_id": user_id, "banned": True}), 200

    # ── POST /admin/users/<user_id>/unban ────────────────────────────

    @bp.route("/users/<user_id>/unban", methods=["POST"])
    @require_permission(Permission.UNBAN_USER)
    async def unban_user(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        users.set_banned(user_id=user_id, banned=False)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_UNBANNED,
            target_id=user_id,
            details={},
        )
        return jsonify({"user_id": user_id, "banned": False}), 200

    # ── POST /admin/users/<user_id>/permissions ──────────────────────

    @bp.route("/users/<user_id>/permissions", methods=["POST"])
    @require_permission(Permission.MANAGE_PERMISSIONS)
    async def manage_permission(user_id: str):
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        action = (data.get("action") or "").lower()
        permission = (data.get("permission") or "").upper()
        if action not in {"grant", "revoke"}:
            return bad_request("action must be 'grant' or 'revoke'", "VALIDATION_ERROR")
        if permission not in _VALID_PERMISSIONS:
            return bad_request("Unknown permission", "VALIDATION_ERROR")

        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")

        if action == "grant":
            users.grant_user_permission(user_id=user_id, permission=permission)
            audit_action = ACTION_PERMISSION_GRANTED
        else:
            users.revoke_user_permission(user_id=user_id, permission=permission)
            audit_action = ACTION_PERMISSION_REVOKED

        users.append_audit(
            actor_id=actor.user_id,
            action=audit_action,
            target_id=user_id,
            details={"permission": permission},
        )
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "permissions": sorted(users.get_user_overrides(user_id)),
                    "action": audit_action,
                }
            ),
            200,
        )

    # ── GET /admin/audit ─────────────────────────────────────────────

    @bp.route("/audit", methods=["GET"])
    @require_permission(Permission.VIEW_AUDIT_LOG)
    async def view_audit():
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return bad_request("limit must be an integer", "VALIDATION_ERROR")
        limit = max(1, min(limit, 200))
        entries = users.recent_audit(limit=limit)
        return (
            jsonify(
                {
                    "entries": [
                        {
                            "id": e.id,
                            "actor_id": e.actor_id,
                            "action": e.action,
                            "target_id": e.target_id,
                            "details": e.details,
                            "created_at": e.created_at,
                        }
                        for e in entries
                    ],
                    "count": len(entries),
                }
            ),
            200,
        )

    return bp
