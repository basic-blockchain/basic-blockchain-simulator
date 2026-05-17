"""PostgreSQL adapter for treasury mint ops (Phase 7.8 — BR-TR-*).

Mirrors `InMemoryTreasuryMintOpStore` over the `treasury_mint_ops`
table (migration V022). The `chk_mint_same_signer` `CHECK` is mapped
to `TreasuryMintOpSameSignerError` so the service-layer pre-check and
the DB-layer defence-in-depth raise the same exception type."""

from __future__ import annotations

import secrets
from decimal import Decimal

import psycopg2

from domain.treasury_mint_op import (
    MINT_OP_ID_PREFIX,
    STATUS_CANCELLED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    TreasuryMintOpRecord,
    TreasuryMintOpSameSignerError,
)


_SELECT_COLUMNS = (
    "op_id, status, currency, target_wallet_id, amount, reason, "
    "initiated_by, initiated_at, approved_by, approved_at, "
    "executed_at, cancelled_at, executed_tx_id"
)


class PostgresTreasuryMintOpStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

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
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO treasury_mint_ops ("
                "op_id, status, currency, target_wallet_id, amount, "
                "reason, initiated_by, initiated_at"
                ") VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) "
                "RETURNING initiated_at",
                (
                    op_id,
                    STATUS_PENDING,
                    currency,
                    target_wallet_id,
                    amount,
                    reason,
                    initiated_by,
                ),
            )
            initiated_at = cur.fetchone()[0]
        return TreasuryMintOpRecord(
            op_id=op_id,
            status=STATUS_PENDING,
            currency=currency,
            target_wallet_id=target_wallet_id,
            amount=amount,
            initiated_by=initiated_by,
            initiated_at=str(initiated_at),
            reason=reason,
        )

    def get(self, op_id: str) -> TreasuryMintOpRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM treasury_mint_ops WHERE op_id = %s",
                (op_id,),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TreasuryMintOpRecord]:
        params: list[object] = []
        query = f"SELECT {_SELECT_COLUMNS} FROM treasury_mint_ops"
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
        executed_tx_id: str,
    ) -> TreasuryMintOpRecord | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE treasury_mint_ops "
                    "SET status = %s, approved_by = %s, approved_at = NOW(), "
                    "    executed_at = NOW(), executed_tx_id = %s "
                    "WHERE op_id = %s AND status = %s "
                    f"RETURNING {_SELECT_COLUMNS}",
                    (
                        STATUS_EXECUTED,
                        approver_id,
                        executed_tx_id,
                        op_id,
                        STATUS_PENDING,
                    ),
                )
                row = cur.fetchone()
        except psycopg2.errors.CheckViolation as exc:
            if "chk_mint_same_signer" in str(exc):
                raise TreasuryMintOpSameSignerError(op_id) from exc
            raise
        return _row_to_record(row) if row else None

    def mark_cancelled(
        self, op_id: str
    ) -> TreasuryMintOpRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE treasury_mint_ops "
                "SET status = %s, cancelled_at = NOW() "
                "WHERE op_id = %s AND status = %s "
                f"RETURNING {_SELECT_COLUMNS}",
                (STATUS_CANCELLED, op_id, STATUS_PENDING),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row else None


def _row_to_record(row: tuple) -> TreasuryMintOpRecord:
    return TreasuryMintOpRecord(
        op_id=row[0],
        status=row[1],
        currency=row[2],
        target_wallet_id=row[3],
        amount=Decimal(row[4]),
        reason=row[5],
        initiated_by=row[6],
        initiated_at=str(row[7]),
        approved_by=row[8],
        approved_at=str(row[9]) if row[9] is not None else None,
        executed_at=str(row[10]) if row[10] is not None else None,
        cancelled_at=str(row[11]) if row[11] is not None else None,
        executed_tx_id=row[12],
    )
