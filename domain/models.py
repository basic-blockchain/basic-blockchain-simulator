from __future__ import annotations

from dataclasses import asdict, dataclass


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
    amount: float

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)
