from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import psycopg2

from domain.models import Block, Transaction


# SELECT clause shared between get_all and last so the row layout is identical
# and the Python-side grouping logic does not have to branch.
_BLOCK_TX_SELECT = (
    "SELECT b.index, b.timestamp, b.proof, b.previous_hash, b.merkle_root, "
    "       t.id, t.sender, t.receiver, t.amount "
    "FROM blocks b "
    "LEFT JOIN transactions t ON t.block_index = b.index"
)


def _rows_to_blocks(rows: list[tuple]) -> list[Block]:
    """Group flat (block × tx) rows into Block instances with `transactions`.

    Rows arrive in `(block_index, t.id)` ascending order. Same block index
    repeats per transaction; a block with no transactions appears once with
    NULLs in the t.* columns.
    """
    blocks_by_index: dict[int, Block] = {}
    txs_by_index: dict[int, list[tuple[int, Transaction]]] = defaultdict(list)
    for row in rows:
        block_index = int(row[0])
        if block_index not in blocks_by_index:
            blocks_by_index[block_index] = Block(
                index=block_index,
                timestamp=row[1],
                proof=row[2],
                previous_hash=row[3],
                merkle_root=row[4],
                transactions=[],
            )
        # Detect "no transactions" rows from the LEFT JOIN (t.* are NULL).
        tx_id = row[5]
        if tx_id is None:
            continue
        txs_by_index[block_index].append(
            (
                int(tx_id),
                Transaction(sender=row[6], receiver=row[7], amount=Decimal(row[8])),
            )
        )
    for block_index, indexed_txs in txs_by_index.items():
        indexed_txs.sort(key=lambda pair: pair[0])
        blocks_by_index[block_index].transactions = [tx for _, tx in indexed_txs]
    return [blocks_by_index[i] for i in sorted(blocks_by_index)]


class PostgresBlockRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def get_all(self) -> list[Block]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_BLOCK_TX_SELECT + " ORDER BY b.index ASC, t.id ASC")
            rows = cur.fetchall()
        return _rows_to_blocks(rows)

    def append(self, block: Block) -> None:
        # Block row and its transactions are inserted in the same DB
        # transaction so a crash between the two cannot leave a stamped
        # Merkle root with no matching rows in `transactions` (which would
        # break is_chain_valid on next startup).
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO blocks (index, timestamp, proof, previous_hash, merkle_root) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    block.index,
                    block.timestamp,
                    block.proof,
                    block.previous_hash,
                    block.merkle_root,
                ),
            )
            if block.transactions:
                cur.executemany(
                    "INSERT INTO transactions (block_index, sender, receiver, amount) "
                    "VALUES (%s, %s, %s, %s)",
                    [
                        (block.index, tx.sender, tx.receiver, tx.amount)
                        for tx in block.transactions
                    ],
                )

    def last(self) -> Block:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                _BLOCK_TX_SELECT
                + " WHERE b.index = (SELECT MAX(index) FROM blocks) "
                "ORDER BY t.id ASC"
            )
            rows = cur.fetchall()
        if not rows:
            raise IndexError("No blocks in repository")
        return _rows_to_blocks(rows)[0]

    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM blocks")
            row = cur.fetchone()
        return int(row[0]) if row is not None else 0

    def replace_all(self, blocks: list[Block]) -> None:
        # DELETE on `blocks` cascades to `transactions` via FK, so we always
        # reinsert each block's transactions next to it to keep the two
        # tables consistent with the chain we are loading.
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM blocks")
            for b in blocks:
                cur.execute(
                    "INSERT INTO blocks (index, timestamp, proof, previous_hash, merkle_root) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (b.index, b.timestamp, b.proof, b.previous_hash, b.merkle_root),
                )
                if b.transactions:
                    cur.executemany(
                        "INSERT INTO transactions (block_index, sender, receiver, amount) "
                        "VALUES (%s, %s, %s, %s)",
                        [
                            (b.index, tx.sender, tx.receiver, tx.amount)
                            for tx in b.transactions
                        ],
                    )

    def save_confirmed_transactions(self, block_index: int, txs: list[Transaction]) -> None:
        # Kept for backward compatibility with the v0.9.0 BlockRepositoryProtocol.
        # In the Phase H+ flow `append(block)` already writes a block's
        # transactions atomically, so `_mine` no longer calls this. External
        # callers that explicitly want to backfill rows for a block (e.g.
        # legacy tests or one-off imports) can still invoke it.
        if not txs:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO transactions (block_index, sender, receiver, amount) "
                "VALUES (%s, %s, %s, %s)",
                [(block_index, tx.sender, tx.receiver, tx.amount) for tx in txs],
            )

    def get_confirmed_transactions(self) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT t.block_index, b.timestamp, t.sender, t.receiver, t.amount "
                "FROM transactions t "
                "JOIN blocks b ON b.index = t.block_index "
                "ORDER BY t.block_index ASC, t.id ASC"
            )
            rows = cur.fetchall()
        return [
            {
                "block_index": int(row[0]),
                "block_timestamp": row[1],
                "sender": row[2],
                "receiver": row[3],
                "amount": float(row[4]),
            }
            for row in rows
        ]
