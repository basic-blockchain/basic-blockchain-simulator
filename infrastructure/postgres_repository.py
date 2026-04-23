from __future__ import annotations

import psycopg2

from domain.models import Block


class PostgresBlockRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def get_all(self) -> list[Block]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT index, timestamp, proof, previous_hash FROM blocks ORDER BY index ASC"
            )
            rows = cur.fetchall()
        return [
            Block(index=row[0], timestamp=row[1], proof=row[2], previous_hash=row[3])
            for row in rows
        ]

    def append(self, block: Block) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO blocks (index, timestamp, proof, previous_hash) VALUES (%s, %s, %s, %s)",
                (block.index, block.timestamp, block.proof, block.previous_hash),
            )

    def last(self) -> Block:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT index, timestamp, proof, previous_hash FROM blocks ORDER BY index DESC LIMIT 1"
            )
            row = cur.fetchone()
        if row is None:
            raise IndexError("No blocks in repository")
        return Block(index=row[0], timestamp=row[1], proof=row[2], previous_hash=row[3])

    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM blocks")
            row = cur.fetchone()
        return int(row[0]) if row is not None else 0
