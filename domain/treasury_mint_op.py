"""Treasury mint-op persistence contracts (Phase 7.8 — BR-TR-*).

Backs the above-threshold branch of `POST /admin/mint` and its
approve/cancel/list siblings. See
`docs/specs/7.8.0-treasury-dual-sign.md` §3.2 for the schema and
BR-TR-01..10 in `docs/business-rules.md` §8e for the rules this
repository helps enforce.

A row lands here only when the mint amount meets or exceeds
`MINT_DUAL_SIGN_THRESHOLD` (BR-TR-07); below-threshold mints settle
synchronously and never touch this table."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


MINT_OP_ID_PREFIX = "tmo_"

STATUS_PENDING = "pending_approval"
STATUS_EXECUTED = "executed"
STATUS_CANCELLED = "cancelled"


@dataclass(slots=True)
class TreasuryMintOpRecord:
    op_id: str
    status: str
    currency: str
    target_wallet_id: str
    amount: Decimal
    initiated_by: str
    initiated_at: str
    reason: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    cancelled_at: str | None = None
    executed_tx_id: str | None = None


class TreasuryMintOpNotFoundError(Exception):
    """Raised when an op_id does not resolve to a mint-op row."""


class TreasuryMintOpSameSignerError(Exception):
    """Raised when approver.user_id == initiated_by (BR-TR-01).

    The service layer pre-checks this before calling the repo; the
    PostgreSQL adapter additionally maps the `chk_mint_same_signer`
    `CHECK` violation to this exception as defence in depth."""


class TreasuryMintOpRepositoryProtocol(Protocol):
    def create(
        self,
        *,
        currency: str,
        target_wallet_id: str,
        amount: Decimal,
        initiated_by: str,
        reason: str | None = None,
    ) -> TreasuryMintOpRecord: ...

    def get(self, op_id: str) -> TreasuryMintOpRecord | None: ...

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryMintOpRecord]: ...

    def record_approval_and_execution(
        self,
        op_id: str,
        *,
        approver_id: str,
        executed_tx_id: str,
    ) -> TreasuryMintOpRecord | None:
        """Record the facts of approval and execution atomically:
        transitions `pending_approval` → `executed` and persists the
        approver, both timestamps and the resulting coinbase tx id in
        one update. The caller is responsible for having built and
        submitted the coinbase tx BEFORE invoking this method.

        Returns `None` when the row is no longer in `pending_approval`.
        Raises `TreasuryMintOpSameSignerError` when `approver_id ==
        initiated_by` (BR-TR-01)."""
        ...

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryMintOpRecord | None:
        """Transition `pending_approval` → `cancelled` and return the
        updated record. Returns `None` when the row is no longer
        pending. The initiator-only check (BR-TR-06) is enforced by
        the service layer."""
        ...


class InMemoryTreasuryMintOpStore:
    def __init__(self) -> None:
        self._rows: dict[str, TreasuryMintOpRecord] = {}

    def create(
        self,
        *,
        currency: str,
        target_wallet_id: str,
        amount: Decimal,
        initiated_by: str,
        reason: str | None = None,
    ) -> TreasuryMintOpRecord:
        op_id = MINT_OP_ID_PREFIX + secrets.token_hex(16)
        record = TreasuryMintOpRecord(
            op_id=op_id,
            status=STATUS_PENDING,
            currency=currency,
            target_wallet_id=target_wallet_id,
            amount=amount,
            initiated_by=initiated_by,
            initiated_at=_now_iso(),
            reason=reason,
        )
        self._rows[op_id] = record
        return record

    def get(self, op_id: str) -> TreasuryMintOpRecord | None:
        return self._rows.get(op_id)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryMintOpRecord]:
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
        executed_tx_id: str,
    ) -> TreasuryMintOpRecord | None:
        record = self._rows.get(op_id)
        if record is None or record.status != STATUS_PENDING:
            return None
        if approver_id == record.initiated_by:
            raise TreasuryMintOpSameSignerError(op_id)
        now = _now_iso()
        record.status = STATUS_EXECUTED
        record.approved_by = approver_id
        record.approved_at = now
        record.executed_at = now
        record.executed_tx_id = executed_tx_id
        return record

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryMintOpRecord | None:
        record = self._rows.get(op_id)
        if record is None or record.status != STATUS_PENDING:
            return None
        record.status = STATUS_CANCELLED
        record.cancelled_at = _now_iso()
        return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
