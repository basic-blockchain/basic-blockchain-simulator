from __future__ import annotations

import datetime
import hashlib
import json
from statistics import mean

from .models import Block, Transaction
from .repository import BlockRepositoryProtocol


DIFFICULTY_PREFIX = "00000"


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

    def create_block(self, proof: int, previous_hash: str) -> Block:
        block = Block(
            index=self._repo.count() + 1,
            timestamp=str(datetime.datetime.now()),
            proof=proof,
            previous_hash=previous_hash,
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
        encoded_block = json.dumps(block.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(encoded_block).hexdigest()

    def _validate_blocks(self, blocks: list[Block]) -> bool:
        if not blocks:
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
        self._repo.save_confirmed_transactions(block_index, txs)

    def confirmed_transactions(self) -> list[dict[str, object]]:
        return self._repo.get_confirmed_transactions()

    def chain_as_dicts(self) -> list[dict[str, int | str]]:
        return [block.to_dict() for block in self._repo.get_all()]
