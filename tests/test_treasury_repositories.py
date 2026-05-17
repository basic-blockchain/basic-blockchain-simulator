"""Unit tests for the Phase 7.8.2 treasury repositories (in-memory).

Covers the persistence surface only — service-layer validation
(initiator-only cancel, recipient checks, insufficient funds, audit
emission) lives with the service in Phase 7.8.3."""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.treasury_distribution import (
    DISTRIBUTION_ID_PREFIX,
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    InMemoryTreasuryDistributionStore,
    TreasuryDistributionSameSignerError,
)
from domain.treasury_mint_op import (
    MINT_OP_ID_PREFIX,
    InMemoryTreasuryMintOpStore,
    TreasuryMintOpSameSignerError,
)
from domain.treasury_mint_op import (
    STATUS_CANCELLED as MINT_STATUS_CANCELLED,
)
from domain.treasury_mint_op import (
    STATUS_EXECUTED as MINT_STATUS_EXECUTED,
)
from domain.treasury_mint_op import (
    STATUS_PENDING as MINT_STATUS_PENDING,
)


# ── Distributions ────────────────────────────────────────────────────


def test_distribution_create_returns_pending_record_with_tdo_prefix():
    store = InMemoryTreasuryDistributionStore()

    record = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("100"),
        recipient_user_ids=["usr_a", "usr_b"],
        initiated_by="usr_admin1",
        memo="grants",
    )

    assert record.op_id.startswith(DISTRIBUTION_ID_PREFIX)
    assert record.status == STATUS_PENDING
    assert record.recipient_user_ids == ["usr_a", "usr_b"]
    assert record.memo == "grants"
    assert record.approved_by is None
    assert record.executed_tx_ids is None


def test_distribution_get_returns_none_for_unknown_op_id():
    store = InMemoryTreasuryDistributionStore()
    assert store.get("tdo_missing") is None


def test_distribution_list_filters_by_status_and_orders_desc():
    store = InMemoryTreasuryDistributionStore()
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


def test_distribution_mark_approved_executed_happy_path():
    store = InMemoryTreasuryDistributionStore()
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("50"),
        recipient_user_ids=["u1", "u2"],
        initiated_by="usr_admin1",
    )

    updated = store.mark_approved_executed(
        op.op_id,
        approver_id="usr_admin2",
        executed_tx_ids=["tx_a", "tx_b"],
    )

    assert updated is not None
    assert updated.status == STATUS_EXECUTED
    assert updated.approved_by == "usr_admin2"
    assert updated.approved_at is not None
    assert updated.executed_at is not None
    assert updated.executed_tx_ids == ["tx_a", "tx_b"]


def test_distribution_mark_approved_executed_rejects_same_signer():
    store = InMemoryTreasuryDistributionStore()
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )

    with pytest.raises(TreasuryDistributionSameSignerError):
        store.mark_approved_executed(
            op.op_id,
            approver_id="usr_admin1",
            executed_tx_ids=["tx_a"],
        )

    # State unchanged after rejection.
    assert store.get(op.op_id).status == STATUS_PENDING


def test_distribution_mark_approved_executed_noop_when_already_executed():
    store = InMemoryTreasuryDistributionStore()
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )
    store.mark_approved_executed(
        op.op_id, approver_id="usr_admin2", executed_tx_ids=["tx_a"]
    )

    second = store.mark_approved_executed(
        op.op_id, approver_id="usr_admin3", executed_tx_ids=["tx_b"]
    )
    assert second is None


def test_distribution_mark_cancelled_happy_path():
    store = InMemoryTreasuryDistributionStore()
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


def test_distribution_mark_cancelled_noop_when_not_pending():
    store = InMemoryTreasuryDistributionStore()
    op = store.create(
        currency="USDT",
        source_wallet_id="wal_src",
        amount_per_wallet=Decimal("1"),
        recipient_user_ids=["u1"],
        initiated_by="usr_admin1",
    )
    store.mark_approved_executed(
        op.op_id, approver_id="usr_admin2", executed_tx_ids=["tx_a"]
    )

    assert store.mark_cancelled(op.op_id) is None
    assert store.get(op.op_id).status == STATUS_EXECUTED


# ── Mint ops ─────────────────────────────────────────────────────────


def test_mint_op_create_returns_pending_record_with_tmo_prefix():
    store = InMemoryTreasuryMintOpStore()

    record = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1000000"),
        initiated_by="usr_admin1",
        reason="big mint",
    )

    assert record.op_id.startswith(MINT_OP_ID_PREFIX)
    assert record.status == MINT_STATUS_PENDING
    assert record.amount == Decimal("1000000")
    assert record.reason == "big mint"
    assert record.executed_tx_id is None


def test_mint_op_mark_approved_executed_happy_path():
    store = InMemoryTreasuryMintOpStore()
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )

    updated = store.mark_approved_executed(
        op.op_id,
        approver_id="usr_admin2",
        executed_tx_id="tx_coinbase",
    )

    assert updated is not None
    assert updated.status == MINT_STATUS_EXECUTED
    assert updated.approved_by == "usr_admin2"
    assert updated.executed_tx_id == "tx_coinbase"


def test_mint_op_mark_approved_executed_rejects_same_signer():
    store = InMemoryTreasuryMintOpStore()
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )

    with pytest.raises(TreasuryMintOpSameSignerError):
        store.mark_approved_executed(
            op.op_id,
            approver_id="usr_admin1",
            executed_tx_id="tx_coinbase",
        )


def test_mint_op_mark_cancelled_happy_path():
    store = InMemoryTreasuryMintOpStore()
    op = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )

    updated = store.mark_cancelled(op.op_id)

    assert updated is not None
    assert updated.status == MINT_STATUS_CANCELLED


def test_mint_op_list_filters_by_status_and_orders_desc():
    store = InMemoryTreasuryMintOpStore()
    older = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("1"),
        initiated_by="usr_admin1",
    )
    newer = store.create(
        currency="USDT",
        target_wallet_id="wal_dst",
        amount=Decimal("2"),
        initiated_by="usr_admin1",
    )
    store.mark_approved_executed(
        older.op_id, approver_id="usr_admin2", executed_tx_id="tx1"
    )

    pending = store.list(status=MINT_STATUS_PENDING)
    executed = store.list(status=MINT_STATUS_EXECUTED)

    assert [r.op_id for r in pending] == [newer.op_id]
    assert [r.op_id for r in executed] == [older.op_id]
