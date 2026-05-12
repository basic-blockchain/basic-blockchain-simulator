from __future__ import annotations

import psycopg2

from domain.models import Transaction


class PostgresMempoolRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def add(self, tx: Transaction) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                ""
                "INSERT INTO mempool "
                "(sender, receiver, amount, receiver_amount, sender_wallet_id, receiver_wallet_id, nonce, signature) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                "",
                (
                    tx.sender,
                    tx.receiver,
                    float(tx.amount),
                    float(tx.receiver_amount) if tx.receiver_amount is not None else None,
                    tx.sender_wallet_id,
                    tx.receiver_wallet_id,
                    tx.nonce,
                    tx.signature,
                ),
            )

    def flush(self) -> list[Transaction]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                ""
                "SELECT sender, receiver, amount, receiver_amount, sender_wallet_id, receiver_wallet_id, nonce, signature "
                "FROM mempool ORDER BY created_at, id"
                ""
            )
            rows = cur.fetchall()
            cur.execute("DELETE FROM mempool")
        return [
            Transaction(
                sender=r[0],
                receiver=r[1],
                amount=float(r[2]),
                receiver_amount=float(r[3]) if r[3] is not None else None,
                sender_wallet_id=r[4] or "",
                receiver_wallet_id=r[5] or "",
                nonce=int(r[6] or 0),
                signature=r[7] or "",
            )
            for r in rows
        ]

    def pending(self) -> list[Transaction]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                ""
                "SELECT sender, receiver, amount, receiver_amount, sender_wallet_id, receiver_wallet_id, nonce, signature "
                "FROM mempool ORDER BY created_at, id"
                ""
            )
            rows = cur.fetchall()
        return [
            Transaction(
                sender=r[0],
                receiver=r[1],
                amount=float(r[2]),
                receiver_amount=float(r[3]) if r[3] is not None else None,
                sender_wallet_id=r[4] or "",
                receiver_wallet_id=r[5] or "",
                nonce=int(r[6] or 0),
                signature=r[7] or "",
            )
            for r in rows
        ]

    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM mempool")
            row = cur.fetchone()
        return int(row[0]) if row else 0
