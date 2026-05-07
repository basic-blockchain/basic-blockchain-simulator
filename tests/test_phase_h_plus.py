"""Phase H+ — Block embeds transactions (Merkle-rooted).

Covers:
  - Merkle root determinism and ordering sensitivity (H+A.1)
  - Hash coverage of merkle_root and tamper detection (H+A.3)
  - Mining flow stamps the block with txs and merkle_root (H+A.4)
  - Postgres repo round-trips a block with txs (H+A.6, integration)
  - HTTP `/api/v1/chain` exposes new fields (H+A.7)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain import BlockchainService, MempoolService
from domain.blockchain import EMPTY_MERKLE_ROOT, _compute_merkle_root
from domain.models import Block, Transaction


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── H+A.1 — Merkle determinism ───────────────────────────────────────────────


def test_merkle_root_empty_is_documented_constant():
    assert _compute_merkle_root([]) == EMPTY_MERKLE_ROOT


def test_merkle_root_same_inputs_same_root():
    txs1 = [Transaction("a", "b", 1), Transaction("c", "d", 2)]
    txs2 = [Transaction("a", "b", 1), Transaction("c", "d", 2)]
    assert _compute_merkle_root(txs1) == _compute_merkle_root(txs2)


def test_merkle_root_reorder_changes_root():
    a = Transaction("a", "b", 1)
    c = Transaction("c", "d", 2)
    assert _compute_merkle_root([a, c]) != _compute_merkle_root([c, a])


def test_merkle_root_amount_change_changes_root():
    base = [Transaction("a", "b", 1), Transaction("c", "d", 2)]
    edited = [Transaction("a", "b", 999), Transaction("c", "d", 2)]
    assert _compute_merkle_root(base) != _compute_merkle_root(edited)


def test_merkle_root_handles_odd_count_via_duplication():
    # 3 transactions → level pairs to (h12, h33) → root. Idempotent across runs.
    txs = [Transaction("a", "b", 1), Transaction("c", "d", 2), Transaction("e", "f", 3)]
    root = _compute_merkle_root(txs)
    assert root == _compute_merkle_root(txs)
    assert root != _compute_merkle_root(txs[:2])


# ── H+A.2/H+A.4 — create_block stamps merkle_root and txs ────────────────────


def test_create_block_stamps_merkle_root_and_transactions():
    svc = BlockchainService(difficulty_prefix="0")
    txs = [Transaction("alice", "bob", 5)]
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    block = svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )
    assert block.merkle_root == _compute_merkle_root(txs)
    assert block.transactions == txs


def test_genesis_block_has_empty_merkle_root():
    svc = BlockchainService(difficulty_prefix="0")
    genesis = svc.previous_block()
    assert genesis.merkle_root == EMPTY_MERKLE_ROOT
    assert genesis.transactions == []


# ── H+A.3 — chain validation rejects tampered transactions ───────────────────


def test_is_chain_valid_passes_after_mining_with_txs():
    svc = BlockchainService(difficulty_prefix="0")
    pool = MempoolService()
    pool.add(Transaction("alice", "bob", 1.0))
    pool.add(Transaction("carol", "dave", 2.0))
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    txs = pool.flush()
    svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )
    assert svc.is_chain_valid() is True


def test_is_chain_valid_rejects_tampered_transaction_amount():
    svc = BlockchainService(difficulty_prefix="0")
    pool = MempoolService()
    pool.add(Transaction("alice", "bob", 1.0))
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    txs = pool.flush()
    svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )
    # Tamper: rewrite the amount on the persisted transaction without
    # re-stamping the block's merkle_root.
    svc.chain[-1].transactions[0].amount = type(svc.chain[-1].transactions[0].amount)("999")
    assert svc.is_chain_valid() is False


def test_is_chain_valid_rejects_tampered_merkle_root():
    svc = BlockchainService(difficulty_prefix="0")
    pool = MempoolService()
    pool.add(Transaction("alice", "bob", 1.0))
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    txs = pool.flush()
    svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )
    # Tamper: rewrite the merkle_root on the block without re-hashing.
    svc.chain[-1].merkle_root = "0" * 64
    assert svc.is_chain_valid() is False


# ── H+A.7 — HTTP /api/v1/chain exposes new fields ────────────────────────────


async def test_chain_endpoint_includes_merkle_root_and_transactions():
    module = _load_module()
    async with module.create_app().test_client() as client:
        # Add and mine so chain has more than just genesis.
        await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "bob", "amount": 4.5},
        )
        mine_resp = await client.post("/api/v1/mine_block")
        assert mine_resp.status_code == 200
        body = await mine_resp.get_json()
        assert "merkle_root" in body
        # Phase I.3 expanded Transaction with wallet IDs / nonce /
        # signature; legacy POST /transactions still constructs txs with
        # those fields defaulted, so the response shape now carries
        # them too.
        assert len(body["transactions"]) == 1
        assert body["transactions"][0]["sender"] == "alice"
        assert body["transactions"][0]["receiver"] == "bob"
        assert body["transactions"][0]["amount"] == 4.5

        chain_resp = await client.get("/api/v1/chain")
        chain_body = await chain_resp.get_json()
        assert chain_resp.status_code == 200
        # Genesis + one mined block.
        assert chain_body["length"] == 2
        latest = chain_body["chain"][-1]
        assert "merkle_root" in latest
        assert len(latest["transactions"]) == 1
        assert latest["transactions"][0]["sender"] == "alice"
        # Genesis block is empty.
        genesis = chain_body["chain"][0]
        assert genesis["merkle_root"] == EMPTY_MERKLE_ROOT
        assert genesis["transactions"] == []


# ── H+A.6 — Postgres repo round-trip with txs (integration) ──────────────────


@pytest.mark.integration
def test_postgres_repository_round_trips_block_with_transactions(clean_db):
    from infrastructure.postgres_repository import PostgresBlockRepository

    block_repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=block_repo, difficulty_prefix="0")
    txs = [Transaction("eve", "frank", 7.0), Transaction("gina", "henry", 1.5)]
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )

    # Fresh repo → reads from PG, must hydrate transactions and merkle_root.
    fresh = PostgresBlockRepository(clean_db)
    chain = fresh.get_all()
    assert len(chain) == 2  # genesis + mined
    mined = chain[-1]
    assert mined.merkle_root == _compute_merkle_root(txs)
    assert [(t.sender, t.receiver, float(t.amount)) for t in mined.transactions] == [
        ("eve", "frank", 7.0),
        ("gina", "henry", 1.5),
    ]


@pytest.mark.integration
def test_postgres_chain_validates_after_restart(clean_db):
    from infrastructure.postgres_repository import PostgresBlockRepository

    block_repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=block_repo, difficulty_prefix="0")
    txs = [Transaction("eve", "frank", 7.0)]
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(
        proof=proof, previous_hash=svc.hash_block(prev), transactions=txs
    )

    fresh_repo = PostgresBlockRepository(clean_db)
    fresh_svc = BlockchainService(repository=fresh_repo, difficulty_prefix="0")
    assert fresh_svc.is_chain_valid() is True
