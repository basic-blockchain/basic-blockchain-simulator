from __future__ import annotations

from .mempool_repository import InMemoryMempoolRepository, MempoolRepositoryProtocol
from .models import Transaction
from .validation import validate_transaction


class MempoolService:
    def __init__(self, repository: MempoolRepositoryProtocol | None = None) -> None:
        self._repo: MempoolRepositoryProtocol = repository or InMemoryMempoolRepository()

    def add(self, tx: Transaction) -> None:
        validate_transaction(tx)
        self._repo.add(tx)

    def flush(self) -> list[Transaction]:
        return self._repo.flush()

    def pending(self) -> list[Transaction]:
        return self._repo.pending()

    def count(self) -> int:
        return self._repo.count()
