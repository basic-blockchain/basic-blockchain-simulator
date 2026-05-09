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
    ACTION_USER_DELETED,
    ACTION_USER_RESTORED,
    ACTION_USER_UNBANNED,
    ACTION_USER_UPDATED,
    ACTION_WALLET_FROZEN,
    ACTION_WALLET_UNFROZEN,
)
from domain.auth import Role
from domain.permissions import Permission
from domain.user_repository import EmailTakenError, UserRepositoryProtocol
from domain.wallet_repository import WalletRepositoryProtocol


_VALID_ROLES = {r.value for r in Role}
_VALID_PERMISSIONS = {p.value for p in Permission}


def build_admin_blueprint(
    *,
    users: UserRepositoryProtocol,
    wallets: WalletRepositoryProtocol,
) -> Blueprint:
    """Return the `/admin` blueprint bound to a user + wallet repository.

    The repositories are the one place we read users / wallets / roles /
    overrides / audit from, so the routes never reach into the
    persistence layer directly.
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
        frozen: list[str] = []
        for w in wallets.list_user_wallets(user_id):
            if not w.frozen:
                wallets.set_frozen(wallet_id=w.wallet_id, frozen=True)
            frozen.append(w.wallet_id)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_BANNED,
            target_id=user_id,
            details={"frozen_wallets": frozen},
        )
        return jsonify({"user_id": user_id, "banned": True, "frozen_wallets": frozen}), 200

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

    # ── PATCH /admin/users/<user_id> ─────────────────────────────────

    @bp.route("/users/<user_id>", methods=["PATCH"])
    @require_permission(Permission.UPDATE_USER)
    async def update_user(user_id: str):
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        display_name = data.get("display_name")
        email = data.get("email")
        if display_name is not None and (
            not isinstance(display_name, str) or len(display_name) > 255
        ):
            return bad_request(
                "display_name must be a string <= 255 chars",
                "VALIDATION_ERROR",
            )
        if email is not None and (
            not isinstance(email, str) or len(email) > 255
        ):
            return bad_request(
                "email must be a string <= 255 chars",
                "VALIDATION_ERROR",
            )
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        try:
            users.update_user(
                user_id=user_id,
                display_name=display_name.strip() if isinstance(display_name, str) else None,
                email=email.strip() if isinstance(email, str) else None,
            )
        except EmailTakenError:
            return bad_request("Email already in use", "EMAIL_TAKEN")
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_UPDATED,
            target_id=user_id,
            details={"display_name": display_name, "email": email},
        )
        updated = users.get_user_by_id(user_id)
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "display_name": updated.display_name if updated else None,
                    "email": updated.email if updated else None,
                }
            ),
            200,
        )

    # ── DELETE /admin/users/<user_id> (soft-delete) ──────────────────

    @bp.route("/users/<user_id>", methods=["DELETE"])
    @require_permission(Permission.DELETE_USER)
    async def soft_delete_user(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if target.user_id == actor.user_id:
            return bad_request(
                "An admin cannot delete themselves",
                "SELF_ACTION_FORBIDDEN",
            )
        if target.deleted_at:
            return bad_request("User already deleted", "USER_ALREADY_DELETED")
        # Freeze every wallet the user owns first; soft-delete must not
        # leave hot wallets behind that could still send funds out.
        frozen: list[str] = []
        for w in wallets.list_user_wallets(user_id):
            if not w.frozen:
                wallets.set_frozen(wallet_id=w.wallet_id, frozen=True)
            frozen.append(w.wallet_id)
        users.soft_delete_user(user_id)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_DELETED,
            target_id=user_id,
            details={"frozen_wallets": frozen},
        )
        return (
            jsonify(
                {"user_id": user_id, "deleted": True, "frozen_wallets": frozen}
            ),
            200,
        )

    # ── POST /admin/users/<user_id>/restore ──────────────────────────

    @bp.route("/users/<user_id>/restore", methods=["POST"])
    @require_permission(Permission.RESTORE_USER)
    async def restore_user(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if not target.deleted_at:
            return bad_request("User is not deleted", "USER_NOT_DELETED")
        data = await request.get_json(silent=True) or {}
        unfreeze = bool(data.get("unfreeze_wallets", True)) if isinstance(data, dict) else True
        users.restore_user(user_id)
        unfrozen: list[str] = []
        if unfreeze:
            for w in wallets.list_user_wallets(user_id):
                wallets.set_frozen(wallet_id=w.wallet_id, frozen=False)
                unfrozen.append(w.wallet_id)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_USER_RESTORED,
            target_id=user_id,
            details={"unfrozen_wallets": unfrozen},
        )
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "restored": True,
                    "unfrozen_wallets": unfrozen,
                }
            ),
            200,
        )

    # ── GET /admin/wallets ───────────────────────────────────────────

    @bp.route("/wallets", methods=["GET"])
    @require_permission(Permission.VIEW_WALLETS)
    async def list_all_wallets():
        records = wallets.list_all_wallets()
        return (
            jsonify(
                {
                    "wallets": [
                        {
                            "wallet_id": w.wallet_id,
                            "user_id": w.user_id,
                            "username": w.username,
                            "display_name": w.display_name,
                            "currency": w.currency,
                            "balance": str(w.balance),
                            "public_key": w.public_key,
                            "frozen": w.frozen,
                        }
                        for w in records
                    ],
                    "count": len(records),
                }
            ),
            200,
        )

    # ── POST /admin/wallets/<wallet_id>/freeze ───────────────────────

    @bp.route("/wallets/<wallet_id>/freeze", methods=["POST"])
    @require_permission(Permission.FREEZE_WALLET)
    async def freeze_wallet(wallet_id: str):
        actor = require_auth()
        wallet = wallets.get_wallet(wallet_id)
        if wallet is None:
            return bad_request("Wallet not found", "WALLET_NOT_FOUND")
        wallets.set_frozen(wallet_id=wallet_id, frozen=True)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_WALLET_FROZEN,
            target_id=wallet_id,
            details={"user_id": wallet.user_id},
        )
        return jsonify({"wallet_id": wallet_id, "frozen": True}), 200

    # ── POST /admin/wallets/<wallet_id>/unfreeze ─────────────────────

    @bp.route("/wallets/<wallet_id>/unfreeze", methods=["POST"])
    @require_permission(Permission.UNFREEZE_WALLET)
    async def unfreeze_wallet(wallet_id: str):
        actor = require_auth()
        wallet = wallets.get_wallet(wallet_id)
        if wallet is None:
            return bad_request("Wallet not found", "WALLET_NOT_FOUND")
        wallets.set_frozen(wallet_id=wallet_id, frozen=False)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_WALLET_UNFROZEN,
            target_id=wallet_id,
            details={"user_id": wallet.user_id},
        )
        return jsonify({"wallet_id": wallet_id, "frozen": False}), 200

    return bp
