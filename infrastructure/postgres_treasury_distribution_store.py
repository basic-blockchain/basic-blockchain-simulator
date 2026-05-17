"""PostgreSQL adapter for treasury distributions (Phase 7.8 — BR-TR-*).

Mirrors `InMemoryTreasuryDistributionStore` over the
`treasury_distributions` table (migration V021). The
`chk_dist_same_signer` `CHECK` is mapped to
`TreasuryDistributionSameSignerError` so the service-layer pre-check
and the DB-layer defence-in-depth raise the same exception type."""

from __future__ import annotations

import json
import secrets
from decimal import Decimal

import psycopg2

from domain.treasury_distribution import (
    DISTRIBUTION_ID_PREFIX,
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    TreasuryDistributionRecord,
    TreasuryDistributionSameSignerError,
)


_SELECT_COLUMNS = (
    "op_id, status, currency, source_wallet_id, amount_per_wallet, "
    "recipient_user_ids, memo, initiated_by, initiated_at, "
    "approved_by, approved_at, executed_at, cancelled_at, executed_tx_ids"
)


class PostgresTreasuryDistributionStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

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
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO treasury_distributions ("
                "op_id, status, currency, source_wallet_id, "
                "amount_per_wallet, recipient_user_ids, memo, "
                "initiated_by, initiated_at"
                ") VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW()) "
                "RETURNING initiated_at",
                (
                    op_id,
                    STATUS_PENDING,
                    currency,
                    source_wallet_id,
                    amount_per_wallet,
                    json.dumps(list(recipient_user_ids)),
                    memo,
                    initiated_by,
                ),
            )
            initiated_at = cur.fetchone()[0]
        return TreasuryDistributionRecord(
            op_id=op_id,
            status=STATUS_PENDING,
            currency=currency,
            source_wallet_id=source_wallet_id,
            amount_per_wallet=amount_per_wallet,
            recipient_user_ids=list(recipient_user_ids),
            initiated_by=initiated_by,
            initiated_at=str(initiated_at),
            memo=memo,
        )

    def get(self, op_id: str) -> TreasuryDistributionRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM treasury_distributions WHERE op_id = %s",
                (op_id,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryDistributionRecord]:
        params: list[object] = []
        query = f"SELECT {_SELECT_COLUMNS} FROM treasury_distributions"
        if status is not None:
            query += " WHERE status = %s"
            params.append(status)
        query += " ORDER BY initiated_at DESC LIMIT %s"
        params.append(max(0, limit))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [_row_to_record(r) for r in rows]

    def mark_approved_executed(
        self,
        op_id: str,
        *,
        approver_id: str,
        executed_tx_ids: list[str],
    ) -> TreasuryDistributionRecord | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE treasury_distributions "
                    "SET status = %s, approved_by = %s, approved_at = NOW(), "
                    "    executed_at = NOW(), executed_tx_ids = %s::jsonb "
                    "WHERE op_id = %s AND status = %s "
                    f"RETURNING {_SELECT_COLUMNS}",
                    (
                        STATUS_EXECUTED,
                        approver_id,
                        json.dumps(list(executed_tx_ids)),
                        op_id,
                        STATUS_PENDING,
                    ),
                )
                row = cur.fetchone()
        except psycopg2.errors.CheckViolation as exc:
            if "chk_dist_same_signer" in str(exc):
                raise TreasuryDistributionSameSignerError(op_id) from exc
            raise
        return _row_to_record(row) if row else None

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryDistributionRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE treasury_distributions "
                "SET status = %s, cancelled_at = NOW() "
                "WHERE op_id = %s AND status = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                (STATUS_CANCELLED, op_id, STATUS_PENDING),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None


def _row_to_record(row: tuple) -> TreasuryDistributionRecord:
    return TreasuryDistributionRecord(
        op_id=row[0],
        status=row[1],
        currency=row[2],
        source_wallet_id=row[3],
        amount_per_wallet=Decimal(row[4]),
        recipient_user_ids=list(row[5]) if row[5] is not None else [],
        memo=row[6],
        initiated_by=row[7],
        initiated_at=str(row[8]),
        approved_by=row[9],
        approved_at=str(row[10]) if row[10] is not None else None,
        executed_at=str(row[11]) if row[11] is not None else None,
        cancelled_at=str(row[12]) if row[12] is not None else None,
        executed_tx_ids=list(row[13]) if row[13] is not None else None,
    )
