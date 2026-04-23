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


def _client(registry=None):
    module = _load_module()
    return module.create_app(
        blockchain=BlockchainService(difficulty_prefix="0"),
        mempool=MempoolService(),
        node_registry=registry,
    ).test_client()


# ---------------------------------------------------------------------------
# POST /api/v1/nodes/register
# ---------------------------------------------------------------------------

def test_register_single_node():
    client = _client()
    resp = client.post("/api/v1/nodes/register",
                       json={"nodes": ["http://localhost:5001"]})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["total"] == 1
    assert "http://localhost:5001" in body["nodes"]


def test_register_multiple_nodes():
    client = _client()
    resp = client.post("/api/v1/nodes/register",
                       json={"nodes": ["http://node-a:5001", "http://node-b:5002"]})
    assert resp.status_code == 201
    assert resp.get_json()["total"] == 2


def test_register_normalises_url_without_scheme():
    client = _client()
    resp = client.post("/api/v1/nodes/register",
                       json={"nodes": ["node-c:5003"]})
    assert resp.status_code == 201
    assert "http://node-c:5003" in resp.get_json()["nodes"]


def test_register_rejects_missing_body():
    client = _client()
    resp = client.post("/api/v1/nodes/register",
                       data="not json",
                       content_type="text/plain")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "VALIDATION_ERROR"


def test_register_rejects_missing_nodes_field():
    client = _client()
    resp = client.post("/api/v1/nodes/register", json={"url": "http://x:5000"})
    assert resp.status_code == 400


def test_register_rejects_empty_node_string():
    client = _client()
    resp = client.post("/api/v1/nodes/register", json={"nodes": [""]})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/v1/nodes
# ---------------------------------------------------------------------------

def test_nodes_list_empty_by_default():
    client = _client()
    resp = client.get("/api/v1/nodes")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["nodes"] == []
    assert body["total"] == 0


def test_nodes_list_after_registration():
    reg = InMemoryNodeRegistry()
    reg.add("http://peer:5001")
    client = _client(registry=reg)
    resp = client.get("/api/v1/nodes")
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1


# ---------------------------------------------------------------------------
# GET /api/v1/nodes/resolve
# ---------------------------------------------------------------------------

def test_resolve_returns_not_replaced_when_no_peers():
    client = _client()
    resp = client.get("/api/v1/nodes/resolve")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["replaced"] is False
    assert "chain" in body


def test_resolve_returns_replaced_true_when_longer_chain_found():
    local = BlockchainService(difficulty_prefix="0")
    remote = BlockchainService(difficulty_prefix="0")
    prev = remote.previous_block()
    proof = remote.proof_of_work(prev.proof)
    remote.create_block(proof=proof, previous_hash=remote.hash_block(prev))

    reg = InMemoryNodeRegistry()
    reg.add("http://remote:5001")

    module = _load_module()
    with patch.object(ConsensusService, "_fetch_chain", return_value=remote._repo.get_all()):
        client = module.create_app(
            blockchain=local,
            mempool=MempoolService(),
            node_registry=reg,
        ).test_client()
        resp = client.get("/api/v1/nodes/resolve")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["replaced"] is True
    assert len(body["chain"]) == 2
