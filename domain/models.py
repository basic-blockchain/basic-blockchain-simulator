from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(slots=True)
class Block:
    index: int
    timestamp: str
    proof: int
    previous_hash: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


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
