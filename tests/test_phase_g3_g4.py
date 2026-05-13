from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from domain import InMemoryNodeRegistry, PropagationService
from domain.models import Transaction


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# PropagationService.broadcast_transaction
# ---------------------------------------------------------------------------

def _tx() -> Transaction:
    return Transaction(sender="alice", receiver="bob", amount=5.0)


def test_broadcast_sends_post_to_each_peer():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    reg.add("http://node-b:5002")
    svc = PropagationService(registry=reg)

    with patch.object(svc, "_post") as mock_post:
        svc.broadcast_transaction(_tx())

    urls = {c.args[0] for c in mock_post.call_args_list}
    assert urls == {
        "http://node-a:5001/api/v1/transactions",
        "http://node-b:5002/api/v1/transactions",
    }


def test_broadcast_sends_correct_payload():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    svc = PropagationService(registry=reg)
    tx = _tx()

    with patch.object(svc, "_post") as mock_post:
        svc.broadcast_transaction(tx)

    _, payload = mock_post.call_args.args
    assert payload == tx.to_dict()


def test_broadcast_skips_when_no_peers():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)

    with patch.object(svc, "_post") as mock_post:
        svc.broadcast_transaction(_tx())

    mock_post.assert_not_called()


def test_broadcast_peer_error_does_not_raise():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    svc = PropagationService(registry=reg)

    with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        svc.broadcast_transaction(_tx())  # must not raise


# ---------------------------------------------------------------------------
# PropagationService.notify_resolve
# ---------------------------------------------------------------------------

def test_notify_resolve_calls_get_on_each_peer():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    reg.add("http://node-b:5002")
    svc = PropagationService(registry=reg)

    with patch.object(svc, "_get") as mock_get:
        svc.notify_resolve()

    urls = {c.args[0] for c in mock_get.call_args_list}
    assert urls == {
        "http://node-a:5001/api/v1/nodes/resolve",
        "http://node-b:5002/api/v1/nodes/resolve",
    }


def test_notify_resolve_skips_when_no_peers():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)

    with patch.object(svc, "_get") as mock_get:
        svc.notify_resolve()

    mock_get.assert_not_called()


def test_notify_resolve_peer_error_does_not_raise():
    reg = InMemoryNodeRegistry()
    reg.add("http://node-a:5001")
    svc = PropagationService(registry=reg)

    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        svc.notify_resolve()  # must not raise


# ---------------------------------------------------------------------------
# PropagationService._post / _get — invalid scheme rejected
# ---------------------------------------------------------------------------

def test_post_rejects_non_http_url():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)

    with patch("urllib.request.urlopen") as mock_open:
        svc._post("ftp://evil/path", {})

    mock_open.assert_not_called()


def test_get_rejects_non_http_url():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)

    with patch("urllib.request.urlopen") as mock_open:
        svc._get("ftp://evil/path")

    mock_open.assert_not_called()


def test_post_calls_urlopen_with_correct_headers():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=ctx) as mock_open:
        svc._post("http://node:5001/api/v1/transactions", {"sender": "a"})

    mock_open.assert_called_once()
    req = mock_open.call_args.args[0]
    assert req.get_header("X-propagated") == "1"
    assert req.get_header("Content-type") == "application/json"


def test_get_calls_urlopen_on_valid_url():
    reg = InMemoryNodeRegistry()
    svc = PropagationService(registry=reg)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=ctx) as mock_open:
        svc._get("http://node:5001/api/v1/nodes/resolve")

    mock_open.assert_called_once()


# ---------------------------------------------------------------------------
# API — X-Propagated loop-breaker
# ---------------------------------------------------------------------------

async def test_transaction_propagates_when_no_propagated_header():
    module = _load_module()
    with patch("domain.PropagationService.broadcast_transaction") as mock_bc:
        async with module.create_app().test_client() as client:
            await client.post(
                "/api/v1/transactions",
                json={"sender": "alice", "receiver": "bob", "amount": 1.0},
            )
    mock_bc.assert_called_once()


async def test_transaction_does_not_propagate_with_x_propagated_header():
    module = _load_module()
    with patch("domain.PropagationService.broadcast_transaction") as mock_bc:
        async with module.create_app().test_client() as client:
            await client.post(
                "/api/v1/transactions",
                json={"sender": "alice", "receiver": "bob", "amount": 1.0},
                headers={"X-Propagated": "1"},
            )
    mock_bc.assert_not_called()


async def test_mine_block_triggers_notify_resolve():
    module = _load_module()
    with patch("domain.PropagationService.notify_resolve") as mock_nr:
        async with module.create_app().test_client() as client:
            await client.post("/api/v1/mine_block")
    mock_nr.assert_called_once()
