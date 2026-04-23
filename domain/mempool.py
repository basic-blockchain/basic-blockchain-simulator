from __future__ import annotations

from .models import Transaction
from .validation import validate_transaction


class MempoolService:
    def __init__(self) -> None:
        self._pending: list[Transaction] = []

    def add(self, tx: Transaction) -> None:
        validate_transaction(tx)
        self._pending.append(tx)

    def flush(self) -> list[Transaction]:
        drained = list(self._pending)
        self._pending.clear()
        return drained

    def pending(self) -> list[Transaction]:
        return list(self._pending)

    def count(self) -> int:
        return len(self._pending)
