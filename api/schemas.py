from __future__ import annotations

from decimal import Decimal, InvalidOperation

from domain.models import Transaction

_TRANSACTION_REQUIRED = ("sender", "receiver", "amount")


def parse_transaction(data: dict | None) -> Transaction:
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [f for f in _TRANSACTION_REQUIRED if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    try:
        amount = Decimal(str(data["amount"]))
    except InvalidOperation:
        raise ValueError("'amount' must be a number")

    return Transaction(
        sender=str(data["sender"]),
        receiver=str(data["receiver"]),
        amount=amount,
    )
