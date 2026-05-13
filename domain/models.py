from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True)
class Transaction:
    sender: str
    receiver: str
    amount: Decimal
    receiver_amount: Decimal | None = None
    # Phase I.3 fields — wallet IDs, replay-protection nonce, ECDSA signature.
    # Default to empty strings / 0 so legacy code paths (system-issued
    # coinbase transactions, tests built before I.3) still construct.
    sender_wallet_id: str = ""
    receiver_wallet_id: str = ""
    nonce: int = 0
    signature: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            self.amount = Decimal(str(self.amount))
        if self.receiver_amount is not None and not isinstance(self.receiver_amount, Decimal):
            self.receiver_amount = Decimal(str(self.receiver_amount))

    def to_dict(self) -> dict[str, object]:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": float(self.amount),
            "receiver_amount": float(self.receiver_amount) if self.receiver_amount is not None else None,
            "sender_wallet_id": self.sender_wallet_id,
            "receiver_wallet_id": self.receiver_wallet_id,
            "nonce": self.nonce,
            "signature": self.signature,
        }


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
