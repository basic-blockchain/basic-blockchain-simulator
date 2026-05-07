from __future__ import annotations

import datetime
import hashlib
import json
from statistics import mean

from .models import Block, Transaction
from .repository import BlockRepositoryProtocol


DIFFICULTY_PREFIX = "00000"

# Merkle root for an empty transaction set. Documented constant so callers
# (and reviewers) can verify the boundary case without re-deriving it.
EMPTY_MERKLE_ROOT = hashlib.sha256(b"").hexdigest()


def _compute_merkle_root(txs: list[Transaction]) -> str:
    """Return the Merkle root over the supplied transactions.

    Empty input yields ``EMPTY_MERKLE_ROOT``. Each leaf is the sha256 of the
    transaction's canonical JSON. Internal nodes are sha256 of the
    concatenation of their two child hex digests. Odd-sized levels duplicate
    the last hash before pairing (Bitcoin convention) so the tree is always
    balanced.
    """
    if not txs:
        return EMPTY_MERKLE_ROOT
    level: list[str] = [
        hashlib.sha256(json.dumps(tx.to_dict(), sort_keys=True).encode()).hexdigest()
        for tx in txs
    ]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256((level[i] + level[i + 1]).encode()).hexdigest()
            for i in range(0, len(level), 2)
        ]
    return level[0]


class InMemoryBlockRepository:
    def __init__(self) -> None:
        self._blocks: list[Block] = []
        self._confirmed: list[dict[str, object]] = []

    def get_all(self) -> list[Block]:
        return self._blocks

    def append(self, block: Block) -> None:
        self._blocks.append(block)

    def last(self) -> Block:
        return self._blocks[-1]

    def count(self) -> int:
        return len(self._blocks)

    def replace_all(self, blocks: list[Block]) -> None:
        self._blocks.clear()
        self._blocks.extend(blocks)

    def save_confirmed_transactions(self, block_index: int, txs: list[Transaction]) -> None:
        block = next((b for b in self._blocks if b.index == block_index), None)
        block_timestamp = block.timestamp if block is not None else ""
        for tx in txs:
            self._confirmed.append(
                {
                    "block_index": block_index,
                    "block_timestamp": block_timestamp,
                    "sender": tx.sender,
                    "receiver": tx.receiver,
                    "amount": float(tx.amount),
                }
            )

    def get_confirmed_transactions(self) -> list[dict[str, object]]:
        return list(self._confirmed)


class BlockchainService:
    def __init__(
        self,
        difficulty_prefix: str = DIFFICULTY_PREFIX,
        repository: BlockRepositoryProtocol | None = None,
    ) -> None:
        self._difficulty_prefix = difficulty_prefix
        self._repo: BlockRepositoryProtocol = repository or InMemoryBlockRepository()
        if self._repo.count() == 0:
            self.create_block(proof=1, previous_hash="0")

    @property
    def chain(self) -> list[Block]:
        return self._repo.get_all()

    def create_block(
        self,
        proof: int,
        previous_hash: str,
        transactions: list[Transaction] | None = None,
    ) -> Block:
        txs = list(transactions) if transactions is not None else []
        block = Block(
            index=self._repo.count() + 1,
            timestamp=str(datetime.datetime.now()),
            proof=proof,
            previous_hash=previous_hash,
            merkle_root=_compute_merkle_root(txs),
            transactions=txs,
        )
        self._repo.append(block)
        return block

    def previous_block(self) -> Block:
        return self._repo.last()

    def proof_of_work(self, previous_proof: int) -> int:
        new_proof = 1
        while True:
            hash_operation = hashlib.sha256(
                str(new_proof**2 - previous_proof**2).encode()
            ).hexdigest()
            if hash_operation.startswith(self._difficulty_prefix):
                return new_proof
            new_proof += 1

    def hash_block(self, block: Block) -> str:
        # Hash payload covers the block header *and* the Merkle root, so any
        # change to a transaction (which would change the Merkle root) breaks
        # the chain — but the raw transactions are not redundantly hashed.
        payload = {
            "index": block.index,
            "timestamp": block.timestamp,
            "proof": block.proof,
            "previous_hash": block.previous_hash,
            "merkle_root": block.merkle_root,
        }
        encoded = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _validate_blocks(self, blocks: list[Block]) -> bool:
        if not blocks:
            return False
        # Every block's stored Merkle root must match its actual transactions.
        # Without this check, mutating a row in the `transactions` table would
        # not be detectable from the chain hash alone.
        for block in blocks:
            if _compute_merkle_root(block.transactions) != block.merkle_root:
                return False
        previous_block = blocks[0]
        for block in blocks[1:]:
            if block.previous_hash != self.hash_block(previous_block):
                return False
            hash_operation = hashlib.sha256(
                str(block.proof**2 - previous_block.proof**2).encode()
            ).hexdigest()
            if not hash_operation.startswith(self._difficulty_prefix):
                return False
            previous_block = block
        return True

    def is_chain_valid(self) -> bool:
        return self._validate_blocks(self._repo.get_all())

    def is_valid_chain(self, blocks: list[Block]) -> bool:
        return self._validate_blocks(blocks)

    def replace_chain(self, blocks: list[Block]) -> None:
        self._repo.replace_all(blocks)

    def chain_length(self) -> int:
        return self._repo.count()

    def avg_mine_time_seconds(self) -> float | None:
        blocks = self._repo.get_all()
        if len(blocks) < 2:
            return None
        deltas = []
        for i in range(1, len(blocks)):
            try:
                t0 = datetime.datetime.fromisoformat(str(blocks[i - 1].timestamp))
                t1 = datetime.datetime.fromisoformat(str(blocks[i].timestamp))
                deltas.append((t1 - t0).total_seconds())
            except ValueError:
                continue
        return round(mean(deltas), 3) if deltas else None

    def save_confirmed_transactions(self, block_index: int, txs: list[Transaction]) -> None:
        # Kept for backward compatibility: in the Phase H+ flow, transactions
        # are persisted by the same DB transaction that writes the block (see
        # `repo.append`), so production code no longer calls this. External
        # callers (e.g. legacy test fixtures) can still invoke it.
        self._repo.save_confirmed_transactions(block_index, txs)

    def confirmed_transactions(self) -> list[dict[str, object]]:
        # Single source of truth: the chain itself. Each block carries its
        # transactions (hydrated at read time from the `transactions` table),
        # so the flat history is just a flatten over `chain.blocks[*].transactions`.
        confirmed: list[dict[str, object]] = []
        for block in self._repo.get_all():
            for tx in block.transactions:
                confirmed.append(
                    {
                        "block_index": block.index,
                        "block_timestamp": block.timestamp,
                        "sender": tx.sender,
                        "receiver": tx.receiver,
                        "amount": float(tx.amount),
                    }
                )
        return confirmed

    def chain_as_dicts(self) -> list[dict[str, object]]:
        return [block.to_dict() for block in self._repo.get_all()]
