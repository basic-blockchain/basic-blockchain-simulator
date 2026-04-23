"""
migrations/migrate.py
---------------------
Schema migration runner for the blockchain-simulator project.

Usage:
    python migrations/migrate.py

Environment:
    DATABASE_URL  (optional) — full DSN.
                  Default: postgresql://postgres:postgres@localhost:5432/blockchain_simulator
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATABASE_URL = (
    "postgresql://postgres:postgres@localhost:5432/blockchain_simulator"
)
DATABASE_URL: str = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

# Derive connection components from the DSN so we can connect to the
# maintenance DB ("postgres") first for CREATE DATABASE.
_DSN_PATTERN = re.compile(
    r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)@"
    r"(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<dbname>[^?]+)"
)

VERSIONS_DIR = Path(__file__).parent / "versions"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_dsn(url: str) -> dict[str, str]:
    """Return a dict with keys: user, password, host, port, dbname."""
    m = _DSN_PATTERN.match(url)
    if not m:
        raise ValueError(
            f"DATABASE_URL must be a postgresql:// DSN, got: {url!r}"
        )
    parts = m.groupdict()
    parts["port"] = parts["port"] or "5432"
    return parts


def _ensure_database(parts: dict[str, str]) -> None:
    """Create the target database if it does not already exist."""
    target_db = parts["dbname"]

    # Connect to the maintenance database with autocommit so we can issue
    # CREATE DATABASE outside of a transaction block.
    conn = psycopg2.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        dbname="postgres",
    )
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (target_db,),
            )
            exists = cur.fetchone() is not None

        if not exists:
            with conn.cursor() as cur:
                # sql.Identifier safely quotes the database name.
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(
                        sql.Identifier(target_db)
                    )
                )
            print(f"[migrate] Created database '{target_db}'.")
        else:
            print(f"[migrate] Database '{target_db}' already exists.")
    finally:
        conn.close()


def _collect_versions() -> list[Path]:
    """Return all V*.sql files under versions/, sorted lexicographically."""
    files = sorted(VERSIONS_DIR.glob("V*.sql"))
    if not files:
        print("[migrate] No migration files found in", VERSIONS_DIR)
    return files


def _version_label(path: Path) -> str:
    """Extract the version token (e.g. 'V001') from a filename like
    'V001__create_schema_migrations.sql'."""
    return path.stem.split("__")[0]


def _bootstrap_tracking_table(conn: psycopg2.extensions.connection) -> None:
    """Ensure schema_migrations exists before we try to query it.

    V001 creates it, but if V001 itself has not been applied yet we need the
    table to exist so we can record V001's application inside a transaction
    together with the rest of V001's DDL.  Using CREATE TABLE IF NOT EXISTS
    makes this call idempotent.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT        PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
            )
            """
        )
    conn.commit()


def _applied_versions(conn: psycopg2.extensions.connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _apply_file(
    conn: psycopg2.extensions.connection,
    path: Path,
    version: str,
) -> None:
    """Execute a single SQL migration file inside a transaction."""
    sql_text = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql_text)
    conn.commit()
    print(f"[migrate] Applied {version} ({path.name})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parts = _parse_dsn(DATABASE_URL)

    # Step 1 — guarantee the target database exists.
    _ensure_database(parts)

    # Step 2 — connect to the target database.
    conn = psycopg2.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        dbname=parts["dbname"],
    )

    try:
        # Step 3 — make sure the tracking table is present before querying it.
        _bootstrap_tracking_table(conn)

        # Step 4 — collect pending migrations.
        version_files = _collect_versions()
        if not version_files:
            return

        applied = _applied_versions(conn)
        pending = [
            f for f in version_files
            if _version_label(f) not in applied
        ]

        if not pending:
            print("[migrate] Schema is up to date.")
            return

        # Step 5 — apply each pending file in its own transaction.
        for path in pending:
            version = _version_label(path)
            try:
                _apply_file(conn, path, version)
            except Exception as exc:
                conn.rollback()
                print(
                    f"[migrate] ERROR applying {version}: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
