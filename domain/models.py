from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class Transaction:
    sender: str
    receiver: str
    amount: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))

    def to_dict(self) -> dict[str, float | str]:
        return {"sender": self.sender, "receiver": self.receiver, "amount": float(self.amount)}


@dataclass(slots=True)
class Block:
    index: int
    timestamp: str
    proof: int
    previous_hash: str
    merkle_root: str = ""
    transactions: list[Transaction] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "proof": self.proof,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "transactions": [tx.to_dict() for tx in self.transactions],
        }
