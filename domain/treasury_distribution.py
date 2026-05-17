"""Treasury distribution persistence contracts (Phase 7.8 — BR-TR-*).

Backs `POST /admin/treasury/distribute` and its approve/cancel/list
siblings. See `docs/specs/7.8.0-treasury-dual-sign.md` §3.1 for the
schema and BR-TR-01..10 in `docs/business-rules.md` §8e for the rules
this repository helps enforce.

The service layer (Phase 7.8.3) is responsible for business validation
(initiator-only cancel, same-signer pre-check, recipient validation,
etc.). This module exposes a thin persistence surface — atomic state
transitions and read queries — over the `treasury_distributions`
table (migration V021)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


DISTRIBUTION_ID_PREFIX = "tdo_"

STATUS_PENDING = "pending_approval"
STATUS_EXECUTED = "executed"
STATUS_CANCELLED = "cancelled"


@dataclass(slots=True)
class TreasuryDistributionRecord:
    op_id: str
    status: str
    currency: str
    source_wallet_id: str
    amount_per_wallet: Decimal
    recipient_user_ids: list[str]
    initiated_by: str
    initiated_at: str
    memo: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    cancelled_at: str | None = None
    executed_tx_ids: list[str] | None = None


class TreasuryDistributionNotFoundError(Exception):
    """Raised when an op_id does not resolve to a distribution row."""


class TreasuryDistributionSameSignerError(Exception):
    """Raised when approver.user_id == initiated_by (BR-TR-01).

    The service layer pre-checks this before calling the repo; the
    PostgreSQL adapter additionally maps the `chk_dist_same_signer`
    `CHECK` violation to this exception as defence in depth."""


class TreasuryDistributionRepositoryProtocol(Protocol):
    def create(
        self,
        *,
        currency: str,
        source_wallet_id: str,
        amount_per_wallet: Decimal,
        recipient_user_ids: list[str],
        initiated_by: str,
        memo: str | None = None,
    ) -> TreasuryDistributionRecord: ...

    def get(self, op_id: str) -> TreasuryDistributionRecord | None: ...

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryDistributionRecord]: ...

    def record_approval_and_execution(
        self,
        op_id: str,
        *,
        approver_id: str,
        executed_tx_ids: list[str],
    ) -> TreasuryDistributionRecord | None:
        """Record the facts of approval and execution atomically:
        transitions `pending_approval` → `executed` and persists the
        approver, both timestamps and the resulting tx ids in one
        update. The caller is responsible for having submitted the
        N transfers to the mempool BEFORE invoking this method — the
        repo only records what already happened on the chain side.

        Returns `None` when the row is no longer in `pending_approval`
        (already executed, cancelled, or missing). Raises
        `TreasuryDistributionSameSignerError` when `approver_id ==
        initiated_by` (BR-TR-01)."""
        ...

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryDistributionRecord | None:
        """Transition `pending_approval` → `cancelled` and return the
        updated record. Returns `None` when the row is no longer
        pending. The initiator-only check (BR-TR-06) is enforced by
        the service layer."""
        ...


class InMemoryTreasuryDistributionStore:
    def __init__(self) -> None:
        self._rows: dict[str, TreasuryDistributionRecord] = {}

    def create(
        self,
        *,
        currency: str,
        source_wallet_id: str,
        amount_per_wallet: Decimal,
        recipient_user_ids: list[str],
        initiated_by: str,
        memo: str | None = None,
    ) -> TreasuryDistributionRecord:
        op_id = DISTRIBUTION_ID_PREFIX + secrets.token_hex(16)
        record = TreasuryDistributionRecord(
            op_id=op_id,
            status=STATUS_PENDING,
            currency=currency,
            source_wallet_id=source_wallet_id,
            amount_per_wallet=amount_per_wallet,
            recipient_user_ids=list(recipient_user_ids),
            initiated_by=initiated_by,
            initiated_at=_now_iso(),
            memo=memo,
        )
        self._rows[op_id] = record
        return record

    def get(self, op_id: str) -> TreasuryDistributionRecord | None:
        return self._rows.get(op_id)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryDistributionRecord]:
        rows = list(self._rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: r.initiated_at, reverse=True)
        return rows[: max(0, limit)]

    def record_approval_and_execution(
        self,
        op_id: str,
        *,
        approver_id: str,
        executed_tx_ids: list[str],
    ) -> TreasuryDistributionRecord | None:
        record = self._rows.get(op_id)
        if record is None or record.status != STATUS_PENDING:
            return None
        if approver_id == record.initiated_by:
            raise TreasuryDistributionSameSignerError(op_id)
        now = _now_iso()
        record.status = STATUS_EXECUTED
        record.approved_by = approver_id
        record.approved_at = now
        record.executed_at = now
        record.executed_tx_ids = list(executed_tx_ids)
        return record

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryDistributionRecord | None:
        record = self._rows.get(op_id)
        if record is None or record.status != STATUS_PENDING:
            return None
        record.status = STATUS_CANCELLED
        record.cancelled_at = _now_iso()
        return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
