"""PG integration tests for the Phase 7.8.2 treasury repositories.

Requires a live PostgreSQL with the V021 + V022 migrations applied
(the session-scoped `pg_dsn` fixture in `conftest.py` handles this)."""

from __future__ import annotations

from decimal import Decimal

import psycopg2
import pytest

from domain.treasury_distribution import (
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    TreasuryDistributionSameSignerError,
)
from domain.treasury_mint_op import (
    TreasuryMintOpSameSignerError,
)
from infrastructure.postgres_treasury_distribution_store import (
    PostgresTreasuryDistributionStore,
)
from infrastructure.postgres_treasury_mint_op_store import (
    PostgresTreasuryMintOpStore,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def treasury_db(pg_dsn: str):
    _truncate_treasury(pg_dsn)
    yield pg_dsn
    _truncate_treasury(pg_dsn)


def _truncate_treasury(dsn: str) -> None:
    conn = psycopg2.connect(dsn)
    with conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE treasury_distributions, treasury_mint_ops "
            "RESTART IDENTITY"
        )
    conn.close()


# ── Distributions ────────────────────────────────────────────────────


def test_distribution_round_trip_survives_new_store_instance(treasury_db):
    store = PostgresTreasuryDistributionStore(treasury_db)
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("100"),
        recipient_user_ids=["usr_a", "usr_b"],
        initiated_by="usr_admin1",
        memo="grants",
    )

    reloaded_store = PostgresTreasuryDistributionStore(treasury_db)
    reloaded = reloaded_store.get(op.op_id)

    assert reloaded is not None
    assert reloaded.status == STATUS_PENDING
    assert reloaded.recipient_user_ids == ["usr_a", "usr_b"]
    assert reloaded.amount_per_wallet == Decimal("100")
    assert reloaded.memo == "grants"


def test_distribution_record_approval_and_execution_persists(treasury_db):
    store = PostgresTreasuryDistributionStore(treasury_db)
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )

    updated = store.record_approval_and_execution(
        op.op_id,
        approver_id="usr_admin2",
        executed_tx_ids=["tx_a"],
    )

    assert updated is not None
    assert updated.status == STATUS_EXECUTED
    assert updated.approved_by == "usr_admin2"
    assert updated.executed_tx_ids == ["tx_a"]


def test_distribution_chk_dist_same_signer_enforced_at_db(treasury_db):
    """BR-TR-01 defence-in-depth: even a direct UPDATE bypassing the
    service-layer pre-check must be rejected by the DB."""
    store = PostgresTreasuryDistributionStore(treasury_db)
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )

    with pytest.raises(TreasuryDistributionSameSignerError):
        store.record_approval_and_execution(
            op.op_id,
            approver_id="usr_admin1",
            executed_tx_ids=["tx_a"],
        )

    # The row stayed pending.
    assert store.get(op.op_id).status == STATUS_PENDING


def test_distribution_record_approval_and_execution_noop_when_not_pending(treasury_db):
    store = PostgresTreasuryDistributionStore(treasury_db)
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )
    store.record_approval_and_execution(
        op.op_id, approver_id="usr_admin2", executed_tx_ids=["tx_a"]
    )

    second = store.record_approval_and_execution(
        op.op_id, approver_id="usr_admin3", executed_tx_ids=["tx_b"]
    )
    assert second is None


def test_distribution_mark_cancelled_persists(treasury_db):
    store = PostgresTreasuryDistributionStore(treasury_db)
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )

    updated = store.mark_cancelled(op.op_id)

    assert updated is not None
    assert updated.status == STATUS_CANCELLED
    assert updated.cancelled_at is not None


def test_distribution_list_filters_and_orders(treasury_db):
    store = PostgresTreasuryDistributionStore(treasury_db)
    older = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )
    newer = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("2"),
        recipient_user_ids=["u2"],
        initiated_by="usr_admin1",
    )
    store.mark_cancelled(older.op_id)

    pending = store.list(status=STATUS_PENDING)
    cancelled = store.list(status=STATUS_CANCELLED)

    assert [r.op_id for r in pending] == [newer.op_id]
    assert [r.op_id for r in cancelled] == [older.op_id]


# ── Mint ops ─────────────────────────────────────────────────────────


def test_mint_op_round_trip_survives_new_store_instance(treasury_db):
    store = PostgresTreasuryMintOpStore(treasury_db)
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1000000"),
        initiated_by="usr_admin1",
        reason="big mint",
    )

    reloaded = PostgresTreasuryMintOpStore(treasury_db).get(op.op_id)

    assert reloaded is not None
    assert reloaded.status == STATUS_PENDING
    assert reloaded.amount == Decimal("1000000")
    assert reloaded.target_wallet_id == "wal_dst"
    assert reloaded.reason == "big mint"


def test_mint_op_record_approval_and_execution_persists(treasury_db):
    store = PostgresTreasuryMintOpStore(treasury_db)
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )

    updated = store.record_approval_and_execution(
        op.op_id,
        approver_id="usr_admin2",
        executed_tx_id="tx_coinbase",
    )

    assert updated is not None
    assert updated.status == STATUS_EXECUTED
    assert updated.executed_tx_id == "tx_coinbase"


def test_mint_op_chk_mint_same_signer_enforced_at_db(treasury_db):
    store = PostgresTreasuryMintOpStore(treasury_db)
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )

    with pytest.raises(TreasuryMintOpSameSignerError):
        store.record_approval_and_execution(
            op.op_id,
            approver_id="usr_admin1",
            executed_tx_id="tx_coinbase",
        )

    assert store.get(op.op_id).status == STATUS_PENDING
