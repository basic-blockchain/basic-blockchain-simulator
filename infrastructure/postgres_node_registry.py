from __future__ import annotations

import psycopg2

from domain.node_registry import _normalise


class PostgresNodeRegistry:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def add(self, url: str) -> None:
        normalised = _normalise(url)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO nodes (url) VALUES (%s) ON CONFLICT DO NOTHING",
                (normalised,),
            )

    def all(self) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT url FROM nodes ORDER BY url ASC")
            rows = cur.fetchall()
        return [row[0] for row in rows]

    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM nodes")
            row = cur.fetchone()
        return int(row[0]) if row is not None else 0
