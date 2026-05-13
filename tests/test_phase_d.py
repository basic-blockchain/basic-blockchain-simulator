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


# ---------------------------------------------------------------------------
# Schema validation — POST /api/v1/transactions
# ---------------------------------------------------------------------------

async def test_transaction_rejected_without_json_content_type():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post("/api/v1/transactions", data="not json")
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["code"] == "VALIDATION_ERROR"


async def test_transaction_rejected_when_fields_missing():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post("/api/v1/transactions", json={"sender": "alice"})
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "receiver" in body["error"]
        assert "amount" in body["error"]


async def test_transaction_rejected_when_amount_not_a_number():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "bob", "amount": "not-a-number"},
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["code"] == "VALIDATION_ERROR"


async def test_transaction_rejected_when_sender_equals_receiver():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "alice", "amount": 10.0},
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["code"] == "VALIDATION_ERROR"


async def test_transaction_rejected_when_amount_is_zero():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "bob", "amount": 0},
        )
        assert resp.status_code == 400
        assert (await resp.get_json())["code"] == "VALIDATION_ERROR"


async def test_valid_transaction_returns_201():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "bob", "amount": 42.5},
        )
        assert resp.status_code == 201
        body = await resp.get_json()
        assert body["transaction"]["sender"] == "alice"
        assert body["transaction"]["amount"] == 42.5


# ---------------------------------------------------------------------------
# Rate limiting — POST /api/v1/mine_block
# ---------------------------------------------------------------------------

async def test_mine_block_returns_429_after_rate_limit():
    module = _load_module()
    async with module.create_app().test_client() as client:
        responses = [await client.post("/api/v1/mine_block") for _ in range(6)]
        statuses = [r.status_code for r in responses]
        assert 429 in statuses
        throttled = next(r for r in responses if r.status_code == 429)
        body = await throttled.get_json()
        assert body["code"] == "RATE_LIMITED"
        assert "retry_after_seconds" in body


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

async def test_unknown_route_returns_json_404():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        body = await resp.get_json()
        assert body["code"] == "NOT_FOUND"


async def test_wrong_method_returns_json_405():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/mine_block")
        assert resp.status_code == 405
        body = await resp.get_json()
        assert body["code"] == "METHOD_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Input length validation
# ---------------------------------------------------------------------------

async def test_transaction_rejected_when_sender_exceeds_max_length():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "a" * 256, "receiver": "bob", "amount": 1.0},
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "sender" in body["error"]


async def test_transaction_rejected_when_receiver_exceeds_max_length():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "alice", "receiver": "b" * 256, "amount": 1.0},
        )
        assert resp.status_code == 400
        body = await resp.get_json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "receiver" in body["error"]


async def test_transaction_accepted_at_exact_max_length():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.post(
            "/api/v1/transactions",
            json={"sender": "a" * 255, "receiver": "b" * 255, "amount": 1.0},
        )
        assert resp.status_code == 201
