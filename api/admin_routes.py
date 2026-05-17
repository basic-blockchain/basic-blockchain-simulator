"""Admin endpoints (Phase I.2): list users, grant/revoke roles, ban/unban,
read recent audit entries.

Mounted under `/api/v1/admin`. Every route is gated by
`@require_permission(...)` so the matching ADMIN-default permission must
be present (or granted by override).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Final

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from api.permissions import require_permission
from api.schemas import parse_currency_code
from config import DASHBOARD_QUOTE_CURRENCY as _DASHBOARD_QUOTE_CURRENCY
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
    SEVERITIES,
    severity_for,
)
from domain.auth import Role, generate_temp_password, hash_password
from domain.blockchain import BlockchainService
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


# Relative-window helpers (BR-AD-08/BR-AD-11/BR-AD-12). Grammar is the
# closed set documented in docs/api-reference.md — anything else
# returns `None` so the caller can surface a VALIDATION_ERROR.
_SINCE_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _parse_since(value: str | None) -> datetime | None:
    """Translate a `since=` token into a UTC cutoff datetime."""
    if not value:
        return None
    delta = _SINCE_WINDOWS.get(value)
    if delta is None:
        return None
    return datetime.now(timezone.utc) - delta


def _created_after(created_at: str, cutoff: datetime) -> bool:
    """Compare an ISO8601 `created_at` string against a UTC cutoff. PG
    surfaces created_at as either a `datetime` or an ISO string, so
    both shapes are accepted defensively."""
    try:
        ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


_COMPARE_WINDOWS: dict[str, timedelta] = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

_VOLUME_RANGES: dict[str, timedelta] = {
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1y":  timedelta(days=365),
}

_VOLUME_DEFAULT_BUCKET: dict[str, str] = {
    "30d": "day", "90d": "week", "1y": "week",
}

_MOVEMENTS_RANGES: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d":  timedelta(days=7),
    "30d": timedelta(days=30),
}

# Quote currency the dashboard converts every balance / movement
# into. Resolved at import time from `config.DASHBOARD_QUOTE_CURRENCY`
# (Phase 6i.1) — the constant name keeps the historical "USD" label
# because the response field is still `balance_usd` and USDT/USDC peg
# 1:1 with USD; only the rate-lookup target changes.
_USD_CURRENCY: Final[str] = _DASHBOARD_QUOTE_CURRENCY


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp into UTC-aware datetime; None on fail."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _delta_block(current: int, previous: int) -> dict[str, object]:
    """Build a {current, previous, delta_abs, delta_pct} dict for an
    integer metric. `delta_pct` is `None` when previous == 0 (BR-AD-09)."""
    delta_abs = current - previous
    delta_pct: float | None = None
    if previous != 0:
        delta_pct = round((delta_abs / previous) * 100, 2)
    return {
        "current": current,
        "previous": previous,
        "delta_abs": delta_abs,
        "delta_pct": delta_pct,
    }


def _bucket_key_for(ts: datetime, *, bucket: str) -> str:
    """ISO date label for a bucket: `YYYY-MM-DD` for day, the Monday of
    the ISO week for week."""
    if bucket == "week":
        monday = ts - timedelta(days=ts.weekday())
        return monday.date().isoformat()
    return ts.date().isoformat()


def _bucket_keys(*, since: datetime, until: datetime, bucket: str) -> list[str]:
    """Pre-built grid of bucket keys covering `[since, until]`."""
    step = timedelta(days=7) if bucket == "week" else timedelta(days=1)
    cursor = since
    keys: list[str] = []
    seen: set[str] = set()
    while cursor <= until:
        k = _bucket_key_for(cursor, bucket=bucket)
        if k not in seen:
            seen.add(k)
            keys.append(k)
        cursor += step
    # Include the final boundary in case the loop stopped one step shy
    final = _bucket_key_for(until, bucket=bucket)
    if final not in seen:
        keys.append(final)
    return keys


def _owner_of(wallets: WalletRepositoryProtocol, wallet_id: str) -> str:
    """Look up the user_id that owns a wallet; empty string when the
    wallet is missing (rare — a tx references a deleted wallet)."""
    if not wallet_id:
        return ""
    rec = wallets.get_wallet(wallet_id)
    return rec.user_id if rec else ""


def _convert_to_usd(
    *,
    currencies: CurrencyRepositoryProtocol,
    from_currency: str,
    amount: Decimal,
    at: datetime,
) -> Decimal | None:
    """Convert an amount to USD using the rate as of `at` (BR-AD-06).
    Returns `None` when no rate exists at-or-before that point."""
    if from_currency == _USD_CURRENCY:
        return amount
    rate = currencies.get_rate_at(
        from_currency=from_currency,
        to_currency=_USD_CURRENCY,
        at=at,
    )
    if rate is None:
        return None
    return (amount * rate.rate).quantize(Decimal("0.01"))


def _iter_confirmed_with_meta(
    *,
    blockchain: BlockchainService,
    wallets: WalletRepositoryProtocol,
    currencies: CurrencyRepositoryProtocol,
    since: datetime,
    until: datetime,
):
    """Yield `(ts, currency, amount, usd_or_None, tx, block_index)` for
    every confirmed transaction whose block timestamp falls in
    `[since, until]`. Currency is resolved from the sender wallet (or
    receiver wallet when sender is missing — e.g. mint/coinbase)."""
    for block in blockchain.chain:
        ts = _parse_iso(block.timestamp)
        if ts is None or not (since <= ts <= until):
            continue
        for tx in block.transactions:
            sender_wallet = wallets.get_wallet(tx.sender_wallet_id) if tx.sender_wallet_id else None
            receiver_wallet = wallets.get_wallet(tx.receiver_wallet_id) if tx.receiver_wallet_id else None
            currency = (
                sender_wallet.currency if sender_wallet
                else (receiver_wallet.currency if receiver_wallet else None)
            )
            if currency is None:
                continue
            amount = Decimal(str(tx.amount))
            usd = _convert_to_usd(
                currencies=currencies,
                from_currency=currency,
                amount=amount,
                at=ts,
            )
            yield ts, currency, amount, usd, tx, block.index


def _iter_confirmed_in_window(
    *,
    blockchain: BlockchainService,
    wallets: WalletRepositoryProtocol,
    currencies: CurrencyRepositoryProtocol,
    since: datetime,
    until: datetime,
):
    """Shorter form of `_iter_confirmed_with_meta` for callers that
    only need (ts, currency, amount, usd)."""
    for ts, currency, amount, usd, _tx, _idx in _iter_confirmed_with_meta(
        blockchain=blockchain, wallets=wallets, currencies=currencies,
        since=since, until=until,
    ):
        yield ts, currency, amount, usd


def _count_tx_in_windows(
    blockchain: BlockchainService,
    *,
    cutoff: datetime,
    window: timedelta,
) -> tuple[int, int]:
    """Return `(current_count, previous_count)` for transactions whose
    block timestamp falls in `[cutoff, now]` and `[cutoff-window, cutoff]`
    respectively. Blocks whose timestamp does not parse are skipped —
    the simulator's coinbase blocks use `str(datetime.datetime.now())`
    which `_parse_iso` handles correctly."""
    now = datetime.now(timezone.utc)
    previous_start = cutoff - window
    current = 0
    previous = 0
    for block in blockchain.chain:
        block_ts = _parse_iso(block.timestamp)
        if block_ts is None:
            continue
        tx_count = len(block.transactions)
        if cutoff <= block_ts <= now:
            current += tx_count
        elif previous_start <= block_ts < cutoff:
            previous += tx_count
    return current, previous


def _delta_block_usd(current: Decimal, previous: Decimal) -> dict[str, object]:
    """Same shape as `_delta_block` but encoded for USD values: every
    number is a stringified Decimal so the JSON never loses precision."""
    delta_abs = current - previous
    delta_pct: float | None = None
    if previous != 0:
        delta_pct = float(round((delta_abs / previous) * 100, 2))
    return {
        "current": str(current),
        "previous": str(previous),
        "delta_abs": str(delta_abs),
        "delta_pct": delta_pct,
    }


def build_admin_blueprint(
    *,
    users: UserRepositoryProtocol,
    wallets: WalletRepositoryProtocol,
    currencies: CurrencyRepositoryProtocol,
    blockchain: BlockchainService,
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
        severity_filter = request.args.get("severity") or None
        since_filter = request.args.get("since") or None
        if severity_filter and severity_filter not in SEVERITIES:
            return bad_request(
                f"severity must be one of {sorted(SEVERITIES)}",
                "SEVERITY_INVALID",
            )
        since_cutoff = _parse_since(since_filter)
        if since_filter and since_cutoff is None:
            return bad_request(
                "since must be one of 1h, 24h, 7d, 30d",
                "VALIDATION_ERROR",
            )
        # Pull a wider window when filtering server-side so the post-filter
        # count still hits `limit`. 5x is a heuristic — fine for the
        # in-memory store and indexed PG query, and capped at 1000 so a
        # critical-only filter on a chatty audit log does not balloon.
        fetch_limit = limit if (severity_filter is None and since_cutoff is None) else min(limit * 5, 1000)
        entries = users.recent_audit(
            limit=fetch_limit,
            action=action_filter,
            actor_id=actor_filter,
            target_id=target_filter,
        )
        out: list[dict[str, object]] = []
        for e in entries:
            sev = severity_for(e.action)
            if severity_filter and sev != severity_filter:
                continue
            if since_cutoff is not None and not _created_after(e.created_at, since_cutoff):
                continue
            out.append(
                {
                    "id": e.id,
                    "actor_id": e.actor_id,
                    "action": e.action,
                    "target_id": e.target_id,
                    "details": e.details,
                    "created_at": e.created_at,
                    "severity": sev,
                }
            )
            if len(out) >= limit:
                break
        return (
            jsonify(
                {
                    "entries": out,
                    "count": len(out),
                    "filters": {
                        "action": action_filter,
                        "actor_id": actor_filter,
                        "target_id": target_filter,
                        "severity": severity_filter,
                        "since": since_filter,
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
        # USD-as-of-now using the same `_convert_to_usd` helper Phase 6e
        # built for confirmed_at lookups. Balances are live so we pass
        # `datetime.now(...)` — wallets with no FX rate keep
        # `balance_usd: null` (BR-AD-07: never silently zero).
        now = datetime.now(timezone.utc)
        total_usd = Decimal("0")
        unpriced_currencies: set[str] = set()
        out = []
        for w in records:
            usd = _convert_to_usd(
                currencies=currencies,
                from_currency=w.currency,
                amount=w.balance,
                at=now,
            )
            if usd is None:
                unpriced_currencies.add(w.currency)
            else:
                total_usd += usd
            out.append(
                {
                    "wallet_id": w.wallet_id,
                    "user_id": w.user_id,
                    "username": w.username,
                    "display_name": w.display_name,
                    "currency": w.currency,
                    "wallet_type": w.wallet_type,
                    "balance": str(w.balance),
                    "balance_usd": str(usd) if usd is not None else None,
                    "public_key": w.public_key,
                    "frozen": w.frozen,
                }
            )
        return (
            jsonify(
                {
                    "wallets": out,
                    "count": len(out),
                    "total_balance_usd": str(total_usd),
                    "unpriced_currencies": sorted(unpriced_currencies),
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
        compare_token = request.args.get("compare") or None
        if compare_token and compare_token not in _COMPARE_WINDOWS:
            return bad_request(
                f"compare must be one of {sorted(_COMPARE_WINDOWS)}",
                "COMPARE_INVALID",
            )

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

        payload: dict[str, object] = {
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

        if compare_token:
            window = _COMPARE_WINDOWS[compare_token]
            cutoff = datetime.now(timezone.utc) - window
            # Users that existed before `cutoff` (i.e. the previous
            # period's "total"). The active/banned/deleted flags are
            # current-only — we approximate "previous active" as
            # "users that existed and are not currently deleted before
            # cutoff" because the store does not snapshot the flags.
            previous_users = [
                u for u in all_users
                if (_parse_iso(u.created_at) or datetime.now(timezone.utc)) <= cutoff
            ]
            previous_total = len(previous_users)
            previous_active = sum(
                1 for u in previous_users
                if not u.banned and u.deleted_at is None
            )

            # Transactions in current vs previous windows. The previous
            # window is the equal-length window immediately before
            # `cutoff`. Volume is summed in NATIVE units (no FX yet —
            # the USD-aggregated version lives on /admin/volume which
            # carries the historical rate lookup; here we report the
            # native count and let the dashboard combine the two).
            current_count, previous_count = _count_tx_in_windows(
                blockchain, cutoff=cutoff, window=window,
            )

            payload["compare"] = {
                "range": compare_token,
                "previous_period_end": cutoff.isoformat(),
                "users": {
                    "total":  _delta_block(total_users, previous_total),
                    "active": _delta_block(active_users, previous_active),
                },
                "transactions": {
                    "count": _delta_block(current_count, previous_count),
                },
            }

        return jsonify(payload), 200

    # ── GET /admin/volume ────────────────────────────────────────────

    @bp.route("/volume", methods=["GET"])
    @require_permission(Permission.VIEW_USERS)
    async def get_volume():
        range_token = request.args.get("range")
        if range_token not in _VOLUME_RANGES:
            return bad_request(
                f"range must be one of {sorted(_VOLUME_RANGES)}",
                "RANGE_INVALID",
            )
        bucket = request.args.get("bucket") or _VOLUME_DEFAULT_BUCKET[range_token]
        if bucket not in {"day", "week"}:
            return bad_request("bucket must be 'day' or 'week'", "VALIDATION_ERROR")

        now = datetime.now(timezone.utc)
        since = now - _VOLUME_RANGES[range_token]

        # Pre-build empty buckets so the response carries a continuous
        # axis (BR-AD-08) regardless of whether any tx fell on each day.
        bucket_keys = _bucket_keys(since=since, until=now, bucket=bucket)
        per_bucket: dict[str, dict[str, object]] = {
            k: {"ts": k, "volume_usd": Decimal("0"), "tx_count": 0, "unpriced_count": 0}
            for k in bucket_keys
        }
        total_usd = Decimal("0")
        total_count = 0
        total_unpriced = 0

        for ts, currency, amount, usd in _iter_confirmed_in_window(
            blockchain=blockchain,
            wallets=wallets,
            currencies=currencies,
            since=since,
            until=now,
        ):
            key = _bucket_key_for(ts, bucket=bucket)
            row = per_bucket.get(key)
            if row is None:
                # Tx fell just inside the window but rounding put its
                # key outside the pre-built grid (rare on day boundary
                # crossings) — append on the fly.
                row = {"ts": key, "volume_usd": Decimal("0"), "tx_count": 0, "unpriced_count": 0}
                per_bucket[key] = row
            row["tx_count"] = int(row["tx_count"]) + 1
            total_count += 1
            if usd is None:
                row["unpriced_count"] = int(row["unpriced_count"]) + 1
                total_unpriced += 1
            else:
                row["volume_usd"] = Decimal(str(row["volume_usd"])) + usd
                total_usd += usd

        series = []
        for k in sorted(per_bucket.keys()):
            row = per_bucket[k]
            series.append(
                {
                    "ts": k,
                    "volume_usd": str(row["volume_usd"]),
                    "tx_count": int(row["tx_count"]),
                    "unpriced_count": int(row["unpriced_count"]),
                }
            )

        return (
            jsonify(
                {
                    "range": range_token,
                    "bucket": bucket,
                    "currency": _USD_CURRENCY,
                    "series": series,
                    "totals": {
                        "volume_usd": str(total_usd),
                        "tx_count": total_count,
                        "unpriced_count": total_unpriced,
                    },
                }
            ),
            200,
        )

    # ── GET /admin/movements/top ─────────────────────────────────────

    @bp.route("/movements/top", methods=["GET"])
    @require_permission(Permission.VIEW_WALLETS)
    async def get_movements_top():
        range_token = request.args.get("range", "24h")
        if range_token not in _MOVEMENTS_RANGES:
            return bad_request(
                f"range must be one of {sorted(_MOVEMENTS_RANGES)}",
                "RANGE_INVALID",
            )
        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            return bad_request("limit must be an integer", "VALIDATION_ERROR")
        limit = max(1, min(limit, 50))

        now = datetime.now(timezone.utc)
        since = now - _MOVEMENTS_RANGES[range_token]

        scored: list[tuple[Decimal, dict[str, object]]] = []
        total_volume = Decimal("0")
        for ts, currency, amount, usd, tx, block_index in _iter_confirmed_with_meta(
            blockchain=blockchain,
            wallets=wallets,
            currencies=currencies,
            since=since,
            until=now,
        ):
            # BR-AD-12: drop unpriced rows from a USD-ranked list.
            if usd is None:
                continue
            sender_user = users.get_user_by_id(_owner_of(wallets, tx.sender_wallet_id))
            receiver_user = users.get_user_by_id(_owner_of(wallets, tx.receiver_wallet_id))
            scored.append(
                (
                    usd,
                    {
                        "tx_id": tx.signature or f"{block_index}:{tx.nonce}",
                        "block_height": block_index,
                        "currency": currency,
                        "amount": str(amount),
                        "amount_usd": str(usd),
                        "from_user_id": sender_user.user_id if sender_user else None,
                        "from_username": sender_user.username if sender_user else None,
                        "to_user_id": receiver_user.user_id if receiver_user else None,
                        "to_username": receiver_user.username if receiver_user else None,
                        "confirmed_at": ts.isoformat(),
                    },
                )
            )
            total_volume += usd

        scored.sort(key=lambda pair: pair[0], reverse=True)
        movements = [row for _, row in scored[:limit]]
        return (
            jsonify(
                {
                    "range": range_token,
                    "movements": movements,
                    "count": len(movements),
                    "limit": limit,
                    "total_volume_usd": str(total_volume),
                }
            ),
            200,
        )

    return bp
