from __future__ import annotations

import os
import subprocess
import sys

# Phase I.1: opt the test process into TESTING mode before any project module
# imports `config.py`, so JWT_SECRET picks up the deterministic test sentinel
# instead of raising "JWT_SECRET is required outside TESTING mode".
os.environ.setdefault("TESTING", "true")

import psycopg2
import pytest


def _test_dsn() -> str:
    base = os.environ.get("DATABASE_URL", "")
    if not base:
        return ""
    head, _, _ = base.rpartition("/")
    return f"{head}/blockchain_simulator_test"


_TEST_DSN = _test_dsn()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires a live PostgreSQL connection")


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    if not _TEST_DSN:
        pytest.skip("DATABASE_URL not set — skipping integration tests")

    # Ensure the test database exists
    maintenance = _TEST_DSN.rpartition("/")[0] + "/postgres"
    try:
        conn = psycopg2.connect(maintenance)
    except Exception as exc:
        pytest.skip(f"Cannot connect to PostgreSQL: {exc}")

    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'blockchain_simulator_test'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE blockchain_simulator_test")
    cur.close()
    conn.close()

    # Apply migrations to the test database
    result = subprocess.run(
        [sys.executable, "migrations/migrate.py"],
        env={**os.environ, "DATABASE_URL": _TEST_DSN},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Migrations failed on test DB: {result.stderr}")

    return _TEST_DSN


@pytest.fixture
def clean_db(pg_dsn: str):
    _truncate(pg_dsn)
    yield pg_dsn
    _truncate(pg_dsn)


def _truncate(dsn: str) -> None:
    conn = psycopg2.connect(dsn)
    with conn, conn.cursor() as cur:
        cur.execute("TRUNCATE blocks, mempool, transactions RESTART IDENTITY CASCADE")
    conn.close()
