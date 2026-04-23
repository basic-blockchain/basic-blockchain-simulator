from __future__ import annotations

import datetime
import hashlib
import json

from .models import Block
from .repository import BlockRepositoryProtocol


DIFFICULTY_PREFIX = "00000"


class InMemoryBlockRepository:
    def __init__(self) -> None:
        self._blocks: list[Block] = []

    def get_all(self) -> list[Block]:
        return self._blocks

    def append(self, block: Block) -> None:
        self._blocks.append(block)

    def last(self) -> Block:
        return self._blocks[-1]

    def count(self) -> int:
        return len(self._blocks)


class BlockchainService:
    def __init__(
        self,
        difficulty_prefix: str = DIFFICULTY_PREFIX,
        repository: BlockRepositoryProtocol | None = None,
    ) -> None:
        self._difficulty_prefix = difficulty_prefix
        self._repo: BlockRepositoryProtocol = repository or InMemoryBlockRepository()
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

    def is_chain_valid(self) -> bool:
        chain = self._repo.get_all()
        previous_block = chain[0]
        block_index = 1

        while block_index < len(chain):
            block = chain[block_index]
            if block.previous_hash != self.hash_block(previous_block):
                return False

            previous_proof = previous_block.proof
            proof = block.proof
            hash_operation = hashlib.sha256(
                str(proof**2 - previous_proof**2).encode()
            ).hexdigest()

            if not hash_operation.startswith(self._difficulty_prefix):
                return False

            previous_block = block
            block_index += 1

        return True

    def chain_as_dicts(self) -> list[dict[str, int | str]]:
        return [block.to_dict() for block in self._repo.get_all()]
