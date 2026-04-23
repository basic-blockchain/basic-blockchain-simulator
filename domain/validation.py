from __future__ import annotations

from .models import Transaction


def validate_transaction(tx: Transaction) -> None:
    if tx.amount <= 0:
        raise ValueError("Transaction amount must be greater than zero")
    if tx.sender.strip() == "":
        raise ValueError("Transaction sender must not be empty")
    if tx.receiver.strip() == "":
        raise ValueError("Transaction receiver must not be empty")
    if tx.sender == tx.receiver:
        raise ValueError("Transaction sender and receiver must differ")
