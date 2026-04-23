from __future__ import annotations

from typing import Protocol

from .models import Transaction


class MempoolRepositoryProtocol(Protocol):
    def add(self, tx: Transaction) -> None: ...
    def flush(self) -> list[Transaction]: ...
    def pending(self) -> list[Transaction]: ...
    def count(self) -> int: ...


class InMemoryMempoolRepository:
    def __init__(self) -> None:
        self._pending: list[Transaction] = []

    def add(self, tx: Transaction) -> None:
        self._pending.append(tx)

    def flush(self) -> list[Transaction]:
        drained = list(self._pending)
        self._pending.clear()
        return drained

    def pending(self) -> list[Transaction]:
        return list(self._pending)

    def count(self) -> int:
        return len(self._pending)
