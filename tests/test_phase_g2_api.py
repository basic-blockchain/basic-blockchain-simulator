from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from domain import BlockchainService, ConsensusService, InMemoryNodeRegistry, MempoolService

MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _app(registry=None):
    module = _load_module()
    return module.create_app(
        blockchain=BlockchainService(difficulty_prefix="0"),
        mempool=MempoolService(),
        node_registry=registry,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/nodes/register
# ---------------------------------------------------------------------------

async def test_register_single_node():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register",
                                 json={"nodes": ["http://localhost:5001"]})
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["total"] == 1
        assert "http://localhost:5001" in body["nodes"]


async def test_register_multiple_nodes():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register",
                                 json={"nodes": ["http://node-a:5001", "http://node-b:5002"]})
        assert resp.status_code == 201
        assert (await resp.get_json())["total"] == 2


async def test_register_normalises_url_without_scheme():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register",
                                 json={"nodes": ["node-c:5003"]})
        assert resp.status_code == 201
        assert "http://node-c:5003" in (await resp.get_json())["nodes"]


async def test_register_rejects_missing_body():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register",
                                 data="not json",
                                 headers={"Content-Type": "text/plain"})
        assert resp.status_code == 400
        assert (await resp.get_json())["code"] == "VALIDATION_ERROR"


async def test_register_rejects_missing_nodes_field():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register", json={"url": "http://x:5000"})
        assert resp.status_code == 400


async def test_register_rejects_empty_node_string():
    async with _app().test_client() as client:
        resp = await client.post("/api/v1/nodes/register", json={"nodes": [""]})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/nodes
# ---------------------------------------------------------------------------

async def test_nodes_list_empty_by_default():
    async with _app().test_client() as client:
        resp = await client.get("/api/v1/nodes")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["nodes"] == []
        assert body["total"] == 0


async def test_nodes_list_after_registration():
    reg = InMemoryNodeRegistry()
    reg.add("http://peer:5001")
    async with _app(registry=reg).test_client() as client:
        resp = await client.get("/api/v1/nodes")
        assert resp.status_code == 200
        assert (await resp.get_json())["total"] == 1


# ---------------------------------------------------------------------------
# GET /api/v1/nodes/resolve
# ---------------------------------------------------------------------------

async def test_resolve_returns_not_replaced_when_no_peers():
    async with _app().test_client() as client:
        resp = await client.get("/api/v1/nodes/resolve")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["replaced"] is False
        assert "chain" in body


async def test_resolve_returns_replaced_true_when_longer_chain_found():
    local = BlockchainService(difficulty_prefix="0")
    remote = BlockchainService(difficulty_prefix="0")
    prev = remote.previous_block()
    proof = remote.proof_of_work(prev.proof)
    remote.create_block(proof=proof, previous_hash=remote.hash_block(prev))

    reg = InMemoryNodeRegistry()
    reg.add("http://remote:5001")

    module = _load_module()
    with patch.object(ConsensusService, "_fetch_chain", return_value=remote._repo.get_all()):
        async with module.create_app(
            blockchain=local,
            mempool=MempoolService(),
            node_registry=reg,
        ).test_client() as client:
            resp = await client.get("/api/v1/nodes/resolve")
            assert resp.status_code == 200
            body = await resp.get_json()
            assert body["replaced"] is True
            assert len(body["chain"]) == 2
