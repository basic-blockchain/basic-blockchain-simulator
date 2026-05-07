from __future__ import annotations

from decimal import Decimal, InvalidOperation

from domain.models import Transaction


_LEGACY_TRANSACTION_REQUIRED = ("sender", "receiver", "amount")
_SIGNED_TRANSACTION_REQUIRED = (
    "sender_wallet_id",
    "receiver_wallet_id",
    "amount",
    "nonce",
    "signature",
)
_MAX_FIELD_LEN = 255


def parse_transaction(data: dict | None) -> Transaction:
    """Parse the legacy v0.10.0 transaction shape (sender/receiver as
    free strings, no signature). Kept for back-compat with the v0.6.0
    frontend that has not yet adopted the wallet contract.

    Phase I.3 introduced `parse_signed_transaction` for the new shape.
    """
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [f for f in _LEGACY_TRANSACTION_REQUIRED if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    sender = str(data["sender"])
    receiver = str(data["receiver"])

    if len(sender) > _MAX_FIELD_LEN:
        raise ValueError(f"'sender' must not exceed {_MAX_FIELD_LEN} characters")
    if len(receiver) > _MAX_FIELD_LEN:
        raise ValueError(f"'receiver' must not exceed {_MAX_FIELD_LEN} characters")

    try:
        amount = Decimal(str(data["amount"]))
    except InvalidOperation:
        raise ValueError("'amount' must be a number")

    return Transaction(sender=sender, receiver=receiver, amount=amount)


def parse_signed_transaction(data: dict | None) -> Transaction:
    """Parse the Phase I.3 transaction shape: wallet IDs + nonce +
    ECDSA signature. The display strings (`sender`, `receiver`) are
    derived later from the wallet owners' usernames; the parser fills
    them with the wallet IDs as a sentinel and the wallet service
    overwrites them when persisting.
    """
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [f for f in _SIGNED_TRANSACTION_REQUIRED if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    sender_wallet_id = str(data["sender_wallet_id"]).strip()
    receiver_wallet_id = str(data["receiver_wallet_id"]).strip()
    signature = str(data["signature"]).strip()
    if not sender_wallet_id or not receiver_wallet_id:
        raise ValueError("Wallet IDs must be non-empty")
    if sender_wallet_id == receiver_wallet_id:
        raise ValueError("Sender and receiver wallets must differ")
    if not signature:
        raise ValueError("Signature must be non-empty")

    try:
        amount = Decimal(str(data["amount"]))
    except InvalidOperation:
        raise ValueError("'amount' must be a number")
    if amount <= 0:
        raise ValueError("'amount' must be positive")

    nonce_raw = data["nonce"]
    if not isinstance(nonce_raw, int) or isinstance(nonce_raw, bool):
        raise ValueError("'nonce' must be an integer")
    if nonce_raw <= 0:
        raise ValueError("'nonce' must be positive")

    return Transaction(
        sender=sender_wallet_id,  # placeholder; service replaces with username
        receiver=receiver_wallet_id,
        amount=amount,
        sender_wallet_id=sender_wallet_id,
        receiver_wallet_id=receiver_wallet_id,
        nonce=nonce_raw,
        signature=signature,
    )
