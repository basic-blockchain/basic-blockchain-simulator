"""Wallet endpoints (Phase I.3).

Mounted under `/api/v1`:

- `POST /wallets`            — create a wallet for the current user.
                               Returns `{wallet_id, public_key, mnemonic}`
                               ONCE; the mnemonic is never persisted.
- `GET  /wallets/me`         — list the current user's wallets.
- `POST /transactions/signed` — submit a signed transfer (new shape).
                               Verifies signature + nonce + balance,
                               admits the tx into the mempool.
- `POST /admin/mint`         — ADMIN-only coinbase credit. Lands as a
                               regular tx in the mempool.
"""

from __future__ import annotations

from decimal import Decimal

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from api.permissions import require_permission
from api.schemas import parse_signed_transaction
from domain.audit import AuditEntry  # noqa: F401 — for type completeness
from domain.mempool import MempoolService
from domain.permissions import Permission
from domain.user_repository import UserRepositoryProtocol
from domain.wallet import MintService, TransferService, WalletService
from domain.wallet_repository import (
    InsufficientBalanceError,
    NonceReplayError,
    WalletFrozenError,
    WalletNotFoundError,
    WalletRepositoryProtocol,
)
from domain.wallet import SignatureRejectedError


def build_wallet_blueprint(
    *,
    wallets: WalletRepositoryProtocol,
    users: UserRepositoryProtocol,
    mempool: MempoolService,
) -> Blueprint:
    bp = Blueprint("wallet", __name__)

    wallet_svc = WalletService(wallets)
    transfer_svc = TransferService(wallets)
    mint_svc = MintService(wallets)

    # ── POST /wallets ────────────────────────────────────────────────

    @bp.route("/wallets", methods=["POST"])
    @require_permission(Permission.CREATE_WALLET)
    async def create_wallet():
        current = require_auth()
        created = wallet_svc.create_wallet(user_id=current.user_id)
        # The mnemonic is returned ONCE here — show this response to the
        # user, ask them to record it, and discard. The server does not
        # persist it.
        return (
            jsonify(
                {
                    "wallet_id": created.wallet_id,
                    "public_key": created.public_key,
                    "mnemonic": created.mnemonic,
                    "warning": (
                        "This mnemonic is shown only once. Store it securely. "
                        "It is the only way to authorise transfers from this wallet."
                    ),
                }
            ),
            201,
        )

    # ── GET /wallets/me ──────────────────────────────────────────────

    @bp.route("/wallets/me", methods=["GET"])
    async def list_my_wallets():
        current = require_auth()
        records = wallet_svc.list_user_wallets(current.user_id)
        return (
            jsonify(
                {
                    "wallets": [
                        {
                            "wallet_id": r.wallet_id,
                            "user_id": r.user_id,
                            "currency": r.currency,
                            "balance": float(r.balance),
                            "public_key": r.public_key,
                            "frozen": r.frozen,
                        }
                        for r in records
                    ],
                    "count": len(records),
                }
            ),
            200,
        )

    # ── POST /transactions/signed ────────────────────────────────────

    @bp.route("/transactions/signed", methods=["POST"])
    @require_permission(Permission.TRANSFER)
    async def submit_signed_transaction():
        current = require_auth()
        data = await request.get_json(silent=True)
        try:
            staged = parse_signed_transaction(data)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")

        # Resolve wallet ownership and usernames.
        sender = wallet_svc.get_wallet(staged.sender_wallet_id)
        receiver = wallet_svc.get_wallet(staged.receiver_wallet_id)
        if sender is None or receiver is None:
            return bad_request("Wallet not found", "WALLET_NOT_FOUND")
        if sender.user_id != current.user_id:
            # The caller does not own the sender wallet. 403 keeps the
            # information that the wallet exists but is not theirs.
            return bad_request("You do not own the sender wallet", "WALLET_OWNERSHIP")
        sender_user = users.get_user_by_id(sender.user_id)
        receiver_user = users.get_user_by_id(receiver.user_id)
        sender_username = sender_user.username if sender_user else sender.user_id
        receiver_username = receiver_user.username if receiver_user else receiver.user_id

        try:
            tx = transfer_svc.build_transaction(
                sender_wallet_id=staged.sender_wallet_id,
                receiver_wallet_id=staged.receiver_wallet_id,
                amount=staged.amount,
                nonce=staged.nonce,
                signature=staged.signature,
                sender_username=sender_username,
                receiver_username=receiver_username,
            )
        except WalletNotFoundError:
            return bad_request("Wallet not found", "WALLET_NOT_FOUND")
        except WalletFrozenError:
            return bad_request("Wallet is frozen", "WALLET_FROZEN")
        except InsufficientBalanceError:
            return bad_request("Insufficient balance", "INSUFFICIENT_BALANCE")
        except SignatureRejectedError:
            return bad_request("Signature does not verify", "SIGNATURE_INVALID")
        except NonceReplayError:
            # 409 in spirit; surfaced as 400 to match the project's
            # uniform error envelope. The dedicated code lets clients
            # branch.
            return bad_request("Nonce already used", "NONCE_REPLAY")

        mempool.add(tx)
        return (
            jsonify({"message": "Transaction admitted", "transaction": tx.to_dict()}),
            201,
        )

    # ── POST /admin/mint ─────────────────────────────────────────────

    @bp.route("/admin/mint", methods=["POST"])
    @require_permission(Permission.MINT)
    async def admin_mint():
        current = require_auth()
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        wallet_id = (data.get("wallet_id") or "").strip()
        if not wallet_id:
            return bad_request("'wallet_id' is required", "VALIDATION_ERROR")
        try:
            amount = Decimal(str(data.get("amount", "")))
        except Exception:  # noqa: BLE001
            return bad_request("'amount' must be a number", "VALIDATION_ERROR")
        if amount <= 0:
            return bad_request("'amount' must be positive", "VALIDATION_ERROR")

        receiver = wallet_svc.get_wallet(wallet_id)
        if receiver is None:
            return bad_request("Wallet not found", "WALLET_NOT_FOUND")
        receiver_user = users.get_user_by_id(receiver.user_id)
        admin_user = users.get_user_by_id(current.user_id)
        receiver_username = receiver_user.username if receiver_user else receiver.user_id
        admin_username = admin_user.username if admin_user else current.user_id

        try:
            tx = mint_svc.build_mint(
                receiver_wallet_id=wallet_id,
                amount=amount,
                receiver_username=receiver_username,
                admin_username=admin_username,
            )
        except WalletFrozenError:
            return bad_request("Wallet is frozen", "WALLET_FROZEN")

        mempool.add(tx)
        return jsonify({"message": "Mint queued", "transaction": tx.to_dict()}), 201

    return bp
