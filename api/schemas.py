from __future__ import annotations

from flask import Request

from domain.models import Transaction

_TRANSACTION_REQUIRED = ("sender", "receiver", "amount")


def parse_transaction(req: Request) -> Transaction:
    if not req.is_json:
        raise ValueError("Content-Type must be application/json")

    payload = req.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [f for f in _TRANSACTION_REQUIRED if f not in payload]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    try:
        amount = float(payload["amount"])
    except (TypeError, ValueError):
        raise ValueError("'amount' must be a number")

    return Transaction(
        sender=str(payload["sender"]),
        receiver=str(payload["receiver"]),
        amount=amount,
    )
