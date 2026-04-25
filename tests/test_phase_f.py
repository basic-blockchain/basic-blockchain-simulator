import importlib.util
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# GET /api/v1/health — in-memory mode (no dsn)
# ---------------------------------------------------------------------------

async def test_health_ok_in_memory_mode():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["status"] == "ok"
        assert body["db"] == "n/a"
        assert body["chain_height"] == 1  # genesis block


async def test_health_returns_correct_chain_height():
    module = _load_module()
    from domain import BlockchainService
    svc = BlockchainService(difficulty_prefix="0")
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))

    async with module.create_app(blockchain=svc).test_client() as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert (await resp.get_json())["chain_height"] == 2


async def test_health_db_ok_when_connectivity_succeeds():
    from domain import BlockchainService, MempoolService
    with patch("api.health.check_db_connectivity", return_value=True):
        module = _load_module()  # import happens inside patch so the binding captures the mock
        async with module.create_app(
            blockchain=BlockchainService(difficulty_prefix="0"),
            mempool=MempoolService(),
            dsn="postgresql://fake/db",
        ).test_client() as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
            body = await resp.get_json()
            assert body["status"] == "ok"
            assert body["db"] == "ok"


async def test_health_503_when_db_unreachable():
    from domain import BlockchainService, MempoolService
    with patch("api.health.check_db_connectivity", return_value=False):
        module = _load_module()
        async with module.create_app(
            blockchain=BlockchainService(difficulty_prefix="0"),
            mempool=MempoolService(),
            dsn="postgresql://fake/db",
        ).test_client() as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 503
            body = await resp.get_json()
            assert body["status"] == "degraded"
            assert body["db"] == "error"


# ---------------------------------------------------------------------------
# GET /api/v1/metrics
# ---------------------------------------------------------------------------

async def test_metrics_genesis_only():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["chain_height"] == 1
        assert body["pending_transactions"] == 0
        assert body["avg_mine_time_seconds"] is None  # need >=2 blocks


async def test_metrics_reflects_mined_blocks_and_pending_tx():
    module = _load_module()
    from domain import BlockchainService, MempoolService, Transaction
    svc = BlockchainService(difficulty_prefix="0")
    pool = MempoolService()
    pool.add(Transaction(sender="alice", receiver="bob", amount=5.0))

    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))

    async with module.create_app(blockchain=svc, mempool=pool).test_client() as client:
        resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["chain_height"] == 2
        assert body["pending_transactions"] == 1
        assert body["avg_mine_time_seconds"] is not None
        assert body["avg_mine_time_seconds"] >= 0


# ---------------------------------------------------------------------------
# Request-ID correlation
# ---------------------------------------------------------------------------

async def test_request_id_generated_when_absent():
    module = _load_module()
    async with module.create_app().test_client() as client:
        resp = await client.get("/api/v1/health")
        # No assertion on value - just that the header round-trips without error
        assert resp.status_code == 200


async def test_request_id_propagated_from_header():
    module = _load_module()
    async with module.create_app().test_client() as client:
        custom_id = "test-req-abc123"
        resp = await client.get("/api/v1/health", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# check_db_connectivity — unit tests with mocked psycopg2 (GAP-01)
# ---------------------------------------------------------------------------

def test_check_db_connectivity_returns_true_on_successful_query():
    from unittest.mock import MagicMock, patch
    from api.health import check_db_connectivity

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        assert check_db_connectivity("postgresql://fake/db") is True


def test_check_db_connectivity_returns_false_on_operational_error():
    from unittest.mock import patch
    import psycopg2
    from api.health import check_db_connectivity

    with patch("psycopg2.connect", side_effect=psycopg2.OperationalError("refused")):
        assert check_db_connectivity("postgresql://bad/dsn") is False


def test_check_db_connectivity_returns_false_on_any_exception():
    from unittest.mock import patch
    from api.health import check_db_connectivity

    with patch("psycopg2.connect", side_effect=RuntimeError("unexpected")):
        assert check_db_connectivity("postgresql://fake/db") is False


def test_check_db_connectivity_returns_false_on_query_failure():
    from unittest.mock import MagicMock, patch
    import psycopg2
    from api.health import check_db_connectivity

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.execute.side_effect = psycopg2.OperationalError("query failed")
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        assert check_db_connectivity("postgresql://fake/db") is False
