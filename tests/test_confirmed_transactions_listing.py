import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def test_confirmed_transactions_endpoint_returns_empty_when_nothing_mined():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/transactions")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body == {"transactions": [], "count": 0}


async def test_confirmed_transactions_endpoint_lists_after_mining():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "bob", "amount": 7.5},
        )
        await client.post(
            "/api/v1/transactions",
            json={"sender": "carol", "receiver": "dave", "amount": 3.25},
        )

        mine_resp = await client.post("/api/v1/mine_block")
        assert mine_resp.status_code == 200
        block_index = (await mine_resp.get_json())["index"]

        resp = await client.get("/api/v1/transactions")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["count"] == 2
        senders = [tx["sender"] for tx in body["transactions"]]
        assert senders == ["alice", "carol"]
        for tx in body["transactions"]:
            assert tx["block_index"] == block_index
            assert "block_timestamp" in tx
            assert tx["block_timestamp"] != ""


async def test_confirmed_transactions_persist_across_pending_flush():
    """After mining, mempool is empty but confirmed transactions remain queryable."""
    module = _load_module()
    async with module.create_app().test_client() as client:
        await client.post(
            "/api/v1/transactions",
            json={"sender": "eve", "receiver": "frank", "amount": 12.0},
        )
        await client.post("/api/v1/mine_block")

        pending_resp = await client.get("/api/v1/transactions/pending")
        assert (await pending_resp.get_json())["count"] == 0

        confirmed_resp = await client.get("/api/v1/transactions")
        body = await confirmed_resp.get_json()
        assert body["count"] == 1
        assert body["transactions"][0]["sender"] == "eve"
        assert body["transactions"][0]["receiver"] == "frank"


def test_blockchain_service_returns_confirmed_transactions_in_memory():
    from domain import BlockchainService, MempoolService
    from domain.models import Transaction

    chain = BlockchainService(difficulty_prefix="0")
    pool = MempoolService()
    pool.add(Transaction(sender="x", receiver="y", amount=1.0))
    prev = chain.previous_block()
    proof = chain.proof_of_work(prev.proof)
    block = chain.create_block(proof=proof, previous_hash=chain.hash_block(prev))
    txs = pool.flush()
    chain.save_confirmed_transactions(block.index, txs)

    confirmed = chain.confirmed_transactions()
    assert len(confirmed) == 1
    record = confirmed[0]
    assert record["sender"] == "x"
    assert record["receiver"] == "y"
    assert record["amount"] == 1.0
    assert record["block_index"] == block.index
    assert record["block_timestamp"] == block.timestamp


@pytest.mark.integration
def test_postgres_repository_returns_confirmed_transactions(clean_db):
    from domain import BlockchainService, MempoolService
    from domain.models import Transaction
    from infrastructure.postgres_mempool_repository import PostgresMempoolRepository
    from infrastructure.postgres_repository import PostgresBlockRepository

    block_repo = PostgresBlockRepository(clean_db)
    mempool_repo = PostgresMempoolRepository(clean_db)
    chain = BlockchainService(repository=block_repo, difficulty_prefix="0")
    pool = MempoolService(repository=mempool_repo)

    pool.add(Transaction(sender="alice", receiver="bob", amount=5.0))
    pool.add(Transaction(sender="carol", receiver="dave", amount=2.5))
    prev = chain.previous_block()
    proof = chain.proof_of_work(prev.proof)
    block = chain.create_block(proof=proof, previous_hash=chain.hash_block(prev))
    txs = pool.flush()
    chain.save_confirmed_transactions(block.index, txs)

    fresh_repo = PostgresBlockRepository(clean_db)
    confirmed = fresh_repo.get_confirmed_transactions()

    assert len(confirmed) == 2
    assert [r["sender"] for r in confirmed] == ["alice", "carol"]
    for record in confirmed:
        assert record["block_index"] == block.index
        assert record["block_timestamp"] == block.timestamp
