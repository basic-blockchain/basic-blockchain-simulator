"""Admin endpoints (Phase I.2): list users, grant/revoke roles, ban/unban,
read recent audit entries.

Mounted under `/api/v1/admin`. Every route is gated by
`@require_permission(...)` so the matching ADMIN-default permission must
be present (or granted by override).
"""

from __future__ import annotations

from decimal import Decimal

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from api.permissions import require_permission
from api.schemas import parse_currency_code
from domain.audit import (
    ACTION_CURRENCY_CREATED,
    ACTION_EXCHANGE_RATE_SET,
    ACTION_PERMISSION_GRANTED,
    ACTION_PERMISSION_REVOKED,
    ACTION_ROLE_GRANTED,
    ACTION_ROLE_PERMISSION_GRANTED,
    ACTION_ROLE_PERMISSION_REVOKED,
    ACTION_ROLE_REVOKED,
    ACTION_TEMP_PASSWORD_ISSUED,
    ACTION_TREASURY_WALLET_CREATED,
    ACTION_USER_BANNED,
    ACTION_USER_DELETED,
    ACTION_USER_RESTORED,
    ACTION_USER_UNBANNED,
    ACTION_USER_UPDATED,
    ACTION_WALLET_FROZEN,
    ACTION_WALLET_UNFROZEN,
)
from domain.auth import Role, generate_temp_password, hash_password
from domain.currency_repository import CurrencyAlreadyExistsError, CurrencyRepositoryProtocol
from domain.permissions import Permission, effective_permissions
from domain.user_repository import EmailTakenError, UserRepositoryProtocol
from domain.wallet import WalletService
from domain.wallet_repository import WalletRepositoryProtocol, WalletType
from infrastructure.exchange_rate_sync import (
    ExchangeRateSyncError,
    ExchangeRateSyncPair,
    PROVIDER_BINANCE,
    PROVIDER_CRYPTO_COM,
    sync_exchange_rates,
)


SYSTEM_USER_ID = "SYSTEM"

_VALID_ROLES = {r.value for r in Role}
_VALID_PERMISSIONS = {p.value for p in Permission}


def build_admin_blueprint(
    *,
    users: UserRepositoryProtocol,
    wallets: WalletRepositoryProtocol,
    currencies: CurrencyRepositoryProtocol,
    bcrypt_rounds: int = 12,
) -> Blueprint:
    """Return the `/admin` blueprint bound to a user + wallet repository.

    The repositories are the one place we read users / wallets / roles /
    overrides / audit from, so the routes never reach into the
    persistence layer directly.
    """
    bp = Blueprint("admin", __name__, url_prefix="/admin")
    wallet_svc = WalletService(wallets)

    # ── GET /admin/users ─────────────────────────────────────────────

    @bp.route("/users", methods=["GET"])
    @require_permission(Permission.VIEW_USERS)
    async def list_users():
        out = []
        for record in users.list_users():
            if record.user_id == SYSTEM_USER_ID:
                continue
            out.append(
                {
                    "user_id": record.user_id,
                    "username": record.username,
                    "display_name": record.display_name,
                    "email": record.email,
                    "banned": record.banned,
                    "deleted_at": record.deleted_at,
                    "country": record.country,
                    "kyc_level": record.kyc_level,
                    "last_active": record.last_active,
                    "created_at": record.created_at,
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

    # ── GET /admin/roles ─────────────────────────────────────────────

    @bp.route("/roles", methods=["GET"])
    @require_permission(Permission.MANAGE_PERMISSIONS)
    async def list_roles():
        role_overrides = users.get_role_overrides()
        result: dict[str, list[str]] = {}
        for role in _VALID_ROLES:
            result[role] = sorted(
                effective_permissions(role=role, role_overrides=role_overrides)
            )
        return jsonify({"roles": result}), 200

    # ── POST /admin/roles/<role>/permissions ─────────────────────────

    @bp.route("/roles/<role>/permissions", methods=["POST"])
    @require_permission(Permission.MANAGE_PERMISSIONS)
    async def manage_role_permission(role: str):
        actor = require_auth()
        if role not in _VALID_ROLES:
            return bad_request(
                f"role must be one of {sorted(_VALID_ROLES)}", "VALIDATION_ERROR"
            )
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        action = (data.get("action") or "").lower()
        permission = (data.get("permission") or "").upper()
        if action not in {"grant", "revoke"}:
            return bad_request("action must be 'grant' or 'revoke'", "VALIDATION_ERROR")
        if permission not in _VALID_PERMISSIONS:
            return bad_request("Unknown permission", "VALIDATION_ERROR")

        if action == "grant":
            users.grant_role_permission(role=role, permission=permission)
            audit_action = ACTION_ROLE_PERMISSION_GRANTED
        else:
            users.revoke_role_permission(role=role, permission=permission)
            audit_action = ACTION_ROLE_PERMISSION_REVOKED

        users.append_audit(
            actor_id=actor.user_id,
            action=audit_action,
            target_id=None,
            details={"role": role, "permission": permission},
        )
        role_overrides = users.get_role_overrides()
        return (
            jsonify(
                {
                    "role": role,
                    "permissions": sorted(
                        effective_permissions(role=role, role_overrides=role_overrides)
                    ),
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
        action_filter = request.args.get("action") or None
        actor_filter = request.args.get("actor_id") or None
        target_filter = request.args.get("target_id") or None
        entries = users.recent_audit(
            limit=limit,
            action=action_filter,
            actor_id=actor_filter,
            target_id=target_filter,
        )
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
                    "filters": {
                        "action": action_filter,
                        "actor_id": actor_filter,
                        "target_id": target_filter,
                    },
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

    # ── POST /admin/users/<user_id>/temp-password ────────────────────────────

    @bp.route("/users/<user_id>/temp-password", methods=["POST"])
    @require_permission(Permission.UPDATE_USER)
    async def issue_temp_password(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if target.deleted_at:
            return bad_request(
                "Cannot issue temp password for a deleted user", "USER_DELETED"
            )
        temp = generate_temp_password()
        users.set_password(
            user_id=user_id,
            password_hash=hash_password(temp, rounds=bcrypt_rounds),
            must_change_password=True,
        )
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_TEMP_PASSWORD_ISSUED,
            target_id=user_id,
            details={},
        )
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "temp_password": temp,
                    "must_change_password": True,
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
                            "wallet_type": w.wallet_type,
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

    # ── POST /admin/wallets/<wallet_id>/top-up ──────────────────────

    @bp.route("/wallets/<wallet_id>/top-up", methods=["POST"])
    @require_permission(Permission.CREATE_TREASURY_WALLET)
    async def top_up_wallet(wallet_id: str):
        require_auth()
        return (
            jsonify(
                {
                    "message": "Treasury top-up is not available yet. See MC-5 in the roadmap.",
                    "wallet_id": wallet_id,
                }
            ),
            501,
        )

    # ── GET /admin/currencies ───────────────────────────────────────

    @bp.route("/currencies", methods=["GET"])
    @require_permission(Permission.CREATE_CURRENCY)
    async def list_currencies():
        active_only = request.args.get("active", "false").lower() == "true"
        records = currencies.list_currencies(active_only=active_only)
        return (
            jsonify(
                {
                    "currencies": [
                        {
                            "code": c.code,
                            "name": c.name,
                            "decimals": c.decimals,
                            "active": c.active,
                        }
                        for c in records
                    ],
                    "count": len(records),
                }
            ),
            200,
        )

    # ── POST /admin/currencies ──────────────────────────────────────

    @bp.route("/currencies", methods=["POST"])
    @require_permission(Permission.CREATE_CURRENCY)
    async def create_currency():
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        try:
            raw_code = data.get("code")
            if raw_code is None or str(raw_code).strip() == "":
                return bad_request("'code' is required", "VALIDATION_ERROR")
            code = parse_currency_code(raw_code)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        name = str(data.get("name") or "").strip()
        if not name:
            return bad_request("'name' is required", "VALIDATION_ERROR")
        decimals_raw = data.get("decimals", 8)
        if not isinstance(decimals_raw, int) or isinstance(decimals_raw, bool):
            return bad_request("'decimals' must be an integer", "VALIDATION_ERROR")
        if decimals_raw < 0 or decimals_raw > 18:
            return bad_request("'decimals' must be between 0 and 18", "VALIDATION_ERROR")
        active = bool(data.get("active", True))
        try:
            currencies.create_currency(
                code=code,
                name=name,
                decimals=decimals_raw,
                active=active,
            )
        except CurrencyAlreadyExistsError:
            return bad_request("Currency already exists", "CURRENCY_EXISTS")

        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_CURRENCY_CREATED,
            target_id=code,
            details={"name": name, "decimals": decimals_raw, "active": active},
        )
        return (
            jsonify({"code": code, "name": name, "decimals": decimals_raw, "active": active}),
            201,
        )

    # ── POST /admin/treasury ────────────────────────────────────────

    @bp.route("/treasury", methods=["POST"])
    @require_permission(Permission.CREATE_TREASURY_WALLET)
    async def create_treasury_wallet():
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        try:
            currency = parse_currency_code(data.get("currency"))
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        record = currencies.get_currency(currency)
        if record is None:
            return bad_request("Currency not found", "CURRENCY_NOT_FOUND")
        if not record.active:
            return bad_request("Currency is not active", "CURRENCY_INACTIVE")

        existing = wallets.find_wallet_by_type_currency(
            wallet_type=WalletType.TREASURY.value,
            currency=currency,
        )
        if existing is not None:
            return bad_request("Treasury wallet already exists", "TREASURY_EXISTS")

        created = wallet_svc.create_wallet(
            user_id=SYSTEM_USER_ID,
            currency=currency,
            wallet_type=WalletType.TREASURY.value,
        )

        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_TREASURY_WALLET_CREATED,
            target_id=created.wallet_id,
            details={"currency": currency},
        )
        return (
            jsonify(
                {
                    "wallet_id": created.wallet_id,
                    "public_key": created.public_key,
                    "currency": currency,
                    "wallet_type": WalletType.TREASURY.value,
                }
            ),
            201,
        )

    # ── GET /admin/exchange-rates ───────────────────────────────────

    @bp.route("/exchange-rates", methods=["GET"])
    @require_permission(Permission.MANAGE_EXCHANGE_RATES)
    async def list_exchange_rates():
        from_currency = request.args.get("from") or None
        to_currency = request.args.get("to") or None
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            return bad_request("limit must be an integer", "VALIDATION_ERROR")
        limit = max(1, min(limit, 200))

        if from_currency:
            try:
                from_currency = parse_currency_code(from_currency)
            except ValueError as exc:
                return bad_request(str(exc), "VALIDATION_ERROR")
        if to_currency:
            try:
                to_currency = parse_currency_code(to_currency)
            except ValueError as exc:
                return bad_request(str(exc), "VALIDATION_ERROR")

        records = currencies.list_exchange_rates(
            from_currency=from_currency,
            to_currency=to_currency,
            limit=limit,
        )
        return (
            jsonify(
                {
                    "rates": [
                        {
                            "rate_id": r.rate_id,
                            "from_currency": r.from_currency,
                            "to_currency": r.to_currency,
                            "rate": str(r.rate),
                            "fee_rate": str(r.fee_rate),
                            "source": r.source,
                            "updated_at": r.updated_at,
                        }
                        for r in records
                    ],
                    "count": len(records),
                }
            ),
            200,
        )

    # ── PUT /admin/exchange-rates/<from>/<to> ───────────────────────

    @bp.route("/exchange-rates/<from_code>/<to_code>", methods=["PUT"])
    @require_permission(Permission.MANAGE_EXCHANGE_RATES)
    async def set_exchange_rate(from_code: str, to_code: str):
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        try:
            from_currency = parse_currency_code(from_code)
            to_currency = parse_currency_code(to_code)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        if from_currency == to_currency:
            return bad_request("Currencies must differ", "VALIDATION_ERROR")

        if currencies.get_currency(from_currency) is None or currencies.get_currency(to_currency) is None:
            return bad_request("Currency not found", "CURRENCY_NOT_FOUND")

        try:
            rate = Decimal(str(data.get("rate", "")))
        except Exception:  # noqa: BLE001
            return bad_request("'rate' must be a number", "VALIDATION_ERROR")
        try:
            fee_rate = Decimal(str(data.get("fee_rate", "0")))
        except Exception:  # noqa: BLE001
            return bad_request("'fee_rate' must be a number", "VALIDATION_ERROR")
        if rate <= 0:
            return bad_request("'rate' must be positive", "VALIDATION_ERROR")
        if fee_rate < 0 or fee_rate > 1:
            return bad_request("'fee_rate' must be between 0 and 1", "VALIDATION_ERROR")

        record = currencies.set_exchange_rate(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            fee_rate=fee_rate,
            source="MANUAL",
        )

        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_EXCHANGE_RATE_SET,
            target_id=f"{from_currency}:{to_currency}",
            details={"rate": str(rate), "fee_rate": str(fee_rate), "source": "MANUAL"},
        )

        return (
            jsonify(
                {
                    "rate_id": record.rate_id,
                    "from_currency": record.from_currency,
                    "to_currency": record.to_currency,
                    "rate": str(record.rate),
                    "fee_rate": str(record.fee_rate),
                    "source": record.source,
                    "updated_at": record.updated_at,
                }
            ),
            201,
        )

    # ── POST /admin/exchange-rates/sync ───────────────────────────

    @bp.route("/exchange-rates/sync", methods=["POST"])
    @require_permission(Permission.MANAGE_EXCHANGE_RATES)
    async def sync_exchange_rates_now():
        actor = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        provider = str(data.get("provider") or PROVIDER_BINANCE).upper()
        if provider not in {PROVIDER_BINANCE, PROVIDER_CRYPTO_COM}:
            return bad_request("Unsupported provider", "VALIDATION_ERROR")

        raw_pairs = data.get("pairs")
        if raw_pairs is None:
            raw_pairs = data.get("pairs_csv")
        if raw_pairs is None:
            return bad_request("'pairs' is required", "VALIDATION_ERROR")

        pairs: list[ExchangeRateSyncPair] = []
        if isinstance(raw_pairs, str):
            candidates = [p.strip() for p in raw_pairs.split(",") if p.strip()]
        elif isinstance(raw_pairs, list):
            candidates = raw_pairs
        else:
            return bad_request("'pairs' must be a list or CSV string", "VALIDATION_ERROR")

        for entry in candidates:
            symbol = None
            if isinstance(entry, str):
                if "/" not in entry:
                    return bad_request(
                        "pair must look like FROM/TO (example: BTC/USDT)",
                        "VALIDATION_ERROR",
                    )
                raw_from, raw_to = [part.strip() for part in entry.split("/", 1)]
            elif isinstance(entry, dict):
                raw_from = entry.get("from") or entry.get("from_currency")
                raw_to = entry.get("to") or entry.get("to_currency")
                symbol = entry.get("symbol") if isinstance(entry.get("symbol"), str) else None
            else:
                return bad_request("pair entries must be strings or objects", "VALIDATION_ERROR")

            try:
                from_currency = parse_currency_code(raw_from)
                to_currency = parse_currency_code(raw_to)
            except ValueError as exc:
                return bad_request(str(exc), "VALIDATION_ERROR")
            if from_currency == to_currency:
                return bad_request("Currencies must differ", "VALIDATION_ERROR")
            if currencies.get_currency(from_currency) is None or currencies.get_currency(to_currency) is None:
                return bad_request("Currency not found", "CURRENCY_NOT_FOUND")

            pairs.append(
                ExchangeRateSyncPair(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    symbol=symbol,
                )
            )

        try:
            records = sync_exchange_rates(
                currencies=currencies,
                pairs=pairs,
                provider=provider,
            )
        except ExchangeRateSyncError as exc:
            return bad_request(str(exc), "EXCHANGE_FEED_ERROR")

        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_EXCHANGE_RATE_SET,
            target_id="SYNC",
            details={
                "provider": provider,
                "pairs": [f"{p.from_currency}/{p.to_currency}" for p in pairs],
                "count": len(records),
                "source": provider,
            },
        )

        return (
            jsonify(
                {
                    "rates": [
                        {
                            "rate_id": r.rate_id,
                            "from_currency": r.from_currency,
                            "to_currency": r.to_currency,
                            "rate": str(r.rate),
                            "fee_rate": str(r.fee_rate),
                            "source": r.source,
                            "updated_at": r.updated_at,
                        }
                        for r in records
                    ],
                    "count": len(records),
                    "provider": provider,
                }
            ),
            200,
        )

    # ── GET /admin/stats ─────────────────────────────────────────────

    @bp.route("/stats", methods=["GET"])
    @require_permission(Permission.VIEW_USERS)
    async def get_stats():
        """Aggregate platform statistics for the admin dashboard KPIs.

        Returns user counts, wallet counts, and total balance per currency
        for USER wallets only (excludes TREASURY / FEE so the figure
        represents real user exposure, not platform reserves).
        """
        all_users = [u for u in users.list_users() if u.user_id != SYSTEM_USER_ID]
        total_users = len(all_users)
        active_users = sum(1 for u in all_users if not u.banned and u.deleted_at is None)
        banned_users = sum(1 for u in all_users if u.banned)
        deleted_users = sum(1 for u in all_users if u.deleted_at is not None)

        all_wallets = wallets.list_all_wallets()
        total_wallets = len(all_wallets)
        user_wallets = [w for w in all_wallets if w.wallet_type == WalletType.USER.value]
        frozen_wallets = sum(1 for w in all_wallets if w.frozen)
        frozen_user_wallets = sum(1 for w in user_wallets if w.frozen)

        balance_by_currency: dict[str, str] = {}
        for w in user_wallets:
            key = w.currency
            balance_by_currency[key] = str(
                Decimal(balance_by_currency.get(key, "0")) + w.balance
            )

        return (
            jsonify(
                {
                    "users": {
                        "total": total_users,
                        "active": active_users,
                        "banned": banned_users,
                        "deleted": deleted_users,
                    },
                    "wallets": {
                        "total": total_wallets,
                        "user_wallets": len(user_wallets),
                        "frozen": frozen_wallets,
                        "frozen_user_wallets": frozen_user_wallets,
                    },
                    "balances": balance_by_currency,
                }
            ),
            200,
        )

    return bp
