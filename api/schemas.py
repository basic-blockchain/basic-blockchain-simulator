from __future__ import annotations

from decimal import Decimal, InvalidOperation

from domain.models import Transaction

_TRANSACTION_REQUIRED = ("sender", "receiver", "amount")
_MAX_FIELD_LEN = 255


def parse_transaction(data: dict | None) -> Transaction:
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [f for f in _TRANSACTION_REQUIRED if f not in data]
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
