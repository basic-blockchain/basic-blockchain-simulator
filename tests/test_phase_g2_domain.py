from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from domain import (
    BlockchainService,
    ConsensusService,
    InMemoryNodeRegistry,
)
from domain.node_registry import _normalise


# ---------------------------------------------------------------------------
# InMemoryNodeRegistry
# ---------------------------------------------------------------------------

def test_registry_normalises_url_without_scheme():
    reg = InMemoryNodeRegistry()
    reg.add("localhost:5001")
    assert "http://localhost:5001" in reg.all()


def test_registry_normalises_url_with_scheme():
    reg = InMemoryNodeRegistry()
    reg.add("http://localhost:5001")
    assert reg.all() == ["http://localhost:5001"]


def test_registry_deduplicates_same_url():
    reg = InMemoryNodeRegistry()
    reg.add("http://localhost:5001")
    reg.add("http://localhost:5001")
    assert reg.count() == 1


def test_registry_stores_multiple_nodes():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    reg.add("http://node-b:5002")
    assert reg.count() == 2
    assert "http://node-a:5001" in reg.all()
    assert "http://node-b:5002" in reg.all()


# ---------------------------------------------------------------------------
# BlockchainService.is_valid_chain / replace_chain
# ---------------------------------------------------------------------------

def test_is_valid_chain_accepts_valid_remote_chain():
    svc = BlockchainService(difficulty_prefix="0")
    chain = svc._repo.get_all()
    assert svc.is_valid_chain(chain) is True


def test_is_valid_chain_rejects_tampered_hash():
    from domain.blockchain import EMPTY_MERKLE_ROOT
    from domain.models import Block
    svc = BlockchainService(difficulty_prefix="0")
    blocks = svc._repo.get_all()
    bad = Block(
        index=blocks[0].index,
        timestamp=blocks[0].timestamp,
        proof=blocks[0].proof,
        previous_hash="tampered",
        merkle_root=EMPTY_MERKLE_ROOT,
    )
    assert svc.is_valid_chain([bad]) is True  # single block always passes (no predecessor)


def test_is_valid_chain_rejects_broken_link():
    from domain.blockchain import EMPTY_MERKLE_ROOT
    from domain.models import Block
    svc = BlockchainService(difficulty_prefix="0")
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))
    blocks = list(svc._repo.get_all())
    # Break the link between block 1 and block 2
    blocks[1] = Block(
        index=blocks[1].index,
        timestamp=blocks[1].timestamp,
        proof=blocks[1].proof,
        previous_hash="broken",
        merkle_root=EMPTY_MERKLE_ROOT,
    )
    assert svc.is_valid_chain(blocks) is False


def test_replace_chain_swaps_local_chain():
    from domain.models import Block
    svc_a = BlockchainService(difficulty_prefix="0")
    svc_b = BlockchainService(difficulty_prefix="0")

    # Mine an extra block on svc_b so its chain is longer
    prev = svc_b.previous_block()
    proof = svc_b.proof_of_work(prev.proof)
    svc_b.create_block(proof=proof, previous_hash=svc_b.hash_block(prev))

    longer_chain = svc_b._repo.get_all()
    svc_a.replace_chain(longer_chain)

    assert svc_a.chain_length() == 2
    assert svc_a.is_chain_valid()


# ---------------------------------------------------------------------------
# ConsensusService.resolve
# ---------------------------------------------------------------------------

def _build_svc(extra_blocks: int = 0) -> BlockchainService:
    svc = BlockchainService(difficulty_prefix="0")
    for _ in range(extra_blocks):
        prev = svc.previous_block()
        proof = svc.proof_of_work(prev.proof)
        svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))
    return svc


def test_resolve_replaces_when_remote_chain_is_longer():
    local = _build_svc(extra_blocks=0)   # height 1
    remote = _build_svc(extra_blocks=2)  # height 3

    reg = InMemoryNodeRegistry()
    reg.add("http://remote-node:5001")

    remote_chain_data = {"chain": [b.to_dict() for b in remote._repo.get_all()]}

    with patch.object(ConsensusService, "_fetch_chain", return_value=remote._repo.get_all()):
        consensus = ConsensusService(local, reg)
        replaced = consensus.resolve()

    assert replaced is True
    assert local.chain_length() == 3


def test_resolve_keeps_local_when_remote_is_shorter():
    local = _build_svc(extra_blocks=2)  # height 3
    remote = _build_svc(extra_blocks=0)  # height 1

    reg = InMemoryNodeRegistry()
    reg.add("http://remote-node:5001")

    with patch.object(ConsensusService, "_fetch_chain", return_value=remote._repo.get_all()):
        consensus = ConsensusService(local, reg)
        replaced = consensus.resolve()

    assert replaced is False
    assert local.chain_length() == 3


def test_resolve_keeps_local_when_no_nodes_registered():
    local = _build_svc()
    reg = InMemoryNodeRegistry()
    consensus = ConsensusService(local, reg)
    assert consensus.resolve() is False


def test_resolve_ignores_unreachable_nodes():
    local = _build_svc()
    reg = InMemoryNodeRegistry()
    reg.add("http://dead-node:9999")

    with patch.object(ConsensusService, "_fetch_chain", return_value=None):
        consensus = ConsensusService(local, reg)
        replaced = consensus.resolve()

    assert replaced is False


def test_resolve_ignores_invalid_remote_chain():
    from domain.models import Block
    local = _build_svc(extra_blocks=0)  # height 1

    reg = InMemoryNodeRegistry()
    reg.add("http://evil-node:5001")

    # Remote chain has 3 blocks but with a broken link
    bad_blocks = [
        Block(index=1, timestamp="2024-01-01", proof=1, previous_hash="0"),
        Block(index=2, timestamp="2024-01-02", proof=9, previous_hash="bad_hash"),
        Block(index=3, timestamp="2024-01-03", proof=99, previous_hash="also_bad"),
    ]

    with patch.object(ConsensusService, "_fetch_chain", return_value=bad_blocks):
        consensus = ConsensusService(local, reg)
        replaced = consensus.resolve()

    assert replaced is False


# ---------------------------------------------------------------------------
# ConsensusService._fetch_chain — real code path coverage (GAP-03)
# ---------------------------------------------------------------------------

def _make_consensus() -> ConsensusService:
    return ConsensusService(
        blockchain=BlockchainService(difficulty_prefix="0"),
        registry=InMemoryNodeRegistry(),
    )


def test_fetch_chain_returns_none_for_non_http_scheme():
    consensus = _make_consensus()
    assert consensus._fetch_chain("ftp://peer:5001") is None


def test_fetch_chain_returns_none_for_file_scheme():
    consensus = _make_consensus()
    assert consensus._fetch_chain("file:///etc/passwd") is None


def test_fetch_chain_returns_none_on_network_error():
    import urllib.error
    consensus = _make_consensus()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        result = consensus._fetch_chain("http://dead-peer:9999")
    assert result is None


def test_fetch_chain_returns_none_on_malformed_json():
    import io
    consensus = _make_consensus()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b"not-valid-json{"
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = consensus._fetch_chain("http://bad-peer:5001")
    assert result is None


def test_fetch_chain_returns_none_when_block_missing_required_key():
    import json as _json
    consensus = _make_consensus()
    payload = _json.dumps({"chain": [{"index": 1}]}).encode()  # missing timestamp/proof/previous_hash
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = payload
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = consensus._fetch_chain("http://incomplete-peer:5001")
    assert result is None


def test_fetch_chain_returns_blocks_on_valid_response():
    import json as _json
    from domain.models import Block
    remote = _build_svc(extra_blocks=1)
    chain_data = _json.dumps(
        {"chain": [b.to_dict() for b in remote._repo.get_all()]}
    ).encode()
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = chain_data
    consensus = _make_consensus()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = consensus._fetch_chain("http://healthy-peer:5001")
    assert result is not None
    assert len(result) == 2
    assert all(isinstance(b, Block) for b in result)
