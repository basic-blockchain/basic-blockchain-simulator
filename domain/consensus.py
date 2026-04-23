from __future__ import annotations

import json
import urllib.request

from .blockchain import BlockchainService
from .models import Block
from .node_registry import NodeRegistryProtocol


class ConsensusService:
    def __init__(
        self,
        blockchain: BlockchainService,
        registry: NodeRegistryProtocol,
        timeout: int = 5,
    ) -> None:
        self._chain = blockchain
        self._registry = registry
        self._timeout = timeout

    def _fetch_chain(self, node_url: str) -> list[Block] | None:
        try:
            url = f"{node_url}/api/v1/chain"
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
            raw_blocks = data.get("chain", [])
            return [
                Block(
                    index=b["index"],
                    timestamp=b["timestamp"],
                    proof=b["proof"],
                    previous_hash=b["previous_hash"],
                )
                for b in raw_blocks
            ]
        except Exception:
            return None

    def resolve(self) -> bool:
        """Replace local chain with the longest valid chain found in the network.

        Returns True if the local chain was replaced, False otherwise.
        """
        best_blocks: list[Block] | None = None
        best_length = self._chain.chain_length()

        for node in self._registry.all():
            remote = self._fetch_chain(node)
            if remote is None:
                continue
            if len(remote) > best_length and self._chain.is_valid_chain(remote):
                best_length = len(remote)
                best_blocks = remote

        if best_blocks is not None:
            self._chain.replace_chain(best_blocks)
            return True
        return False
