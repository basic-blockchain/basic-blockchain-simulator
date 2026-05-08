"""PostgreSQL adapter for the wallet repository (Phase I.3).

Maps `domain/wallet_repository.py` onto the V011 `wallets` and
V012 `wallet_nonces` tables. Each method opens its own connection to
match the existing repos in this package.

Concurrency notes:
- `reserve_nonce` and `apply_transfer` both run under transaction
  isolation level READ COMMITTED (psycopg2's default with
  `with conn:`). Two concurrent transfers on the same wallet race
  on the `wallet_nonces` UPSERT — Postgres serialises that update
  via the row lock, so the loser's `RETURNING` shows it would
  break the monotonic invariant and we raise `NonceReplayError`.
- `apply_transfer` similarly locks the sender row via the UPDATE
  and rolls back on insufficient balance.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg2

from domain.wallet_repository import (
    InsufficientBalanceError,
    NonceReplayError,
    WalletAdminRecord,
    WalletFrozenError,
    WalletNotFoundError,
    WalletRecord,
)


class PostgresWalletStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def create_wallet(
        self,
        *,
        wallet_id: str,
        user_id: str,
        public_key: str,
        currency: str = "NATIVE",
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wallets (wallet_id, user_id, currency, public_key) "
                "VALUES (%s, %s, %s, %s)",
                (wallet_id, user_id, currency, public_key),
            )

    def get_wallet(self, wallet_id: str) -> WalletRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_WALLET_SELECT + " WHERE wallet_id = %s", (wallet_id,))
            row = cur.fetchone()
        return _row_to_wallet(row)

    def list_user_wallets(self, user_id: str) -> list[WalletRecord]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_WALLET_SELECT + " WHERE user_id = %s ORDER BY created_at", (user_id,))
            rows = cur.fetchall()
        return [w for w in (_row_to_wallet(r) for r in rows) if w is not None]

    def set_frozen(self, *, wallet_id: str, frozen: bool) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE wallets SET frozen = %s, updated_at = now() WHERE wallet_id = %s",
                (frozen, wallet_id),
            )
            if cur.rowcount == 0:
                raise WalletNotFoundError(wallet_id)

    def reserve_nonce(self, *, wallet_id: str, nonce: int) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            # Atomic check-and-set: the row is upserted only when the
            # incoming nonce strictly beats `last_used_nonce`. Otherwise
            # the WHERE matches nothing and `rowcount` stays at 0.
            cur.execute(
                """
                INSERT INTO wallet_nonces (wallet_id, last_used_nonce)
                VALUES (%s, %s)
                ON CONFLICT (wallet_id) DO UPDATE
                  SET last_used_nonce = EXCLUDED.last_used_nonce,
                      last_used_at = now()
                  WHERE wallet_nonces.last_used_nonce < EXCLUDED.last_used_nonce
                """,
                (wallet_id, nonce),
            )
            if cur.rowcount == 0:
                raise NonceReplayError(wallet_id)

    def apply_transfer(
        self,
        *,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            # Lock both wallet rows in a deterministic order to avoid a
            # cross-transfer deadlock under concurrent miners.
            ordered = sorted([sender_wallet_id, receiver_wallet_id])
            cur.execute(
                "SELECT wallet_id, balance, frozen FROM wallets "
                "WHERE wallet_id = ANY(%s) FOR UPDATE",
                (ordered,),
            )
            rows = {row[0]: row for row in cur.fetchall()}
            if sender_wallet_id not in rows:
                raise WalletNotFoundError(sender_wallet_id)
            if receiver_wallet_id not in rows:
                raise WalletNotFoundError(receiver_wallet_id)
            sender_row = rows[sender_wallet_id]
            receiver_row = rows[receiver_wallet_id]
            if sender_row[2]:
                raise WalletFrozenError(sender_wallet_id)
            if receiver_row[2]:
                raise WalletFrozenError(receiver_wallet_id)
            if Decimal(sender_row[1]) < amount:
                raise InsufficientBalanceError(sender_wallet_id)
            cur.execute(
                "UPDATE wallets SET balance = balance - %s, updated_at = now() "
                "WHERE wallet_id = %s",
                (amount, sender_wallet_id),
            )
            cur.execute(
                "UPDATE wallets SET balance = balance + %s, updated_at = now() "
                "WHERE wallet_id = %s",
                (amount, receiver_wallet_id),
            )

    def credit(self, *, wallet_id: str, amount: Decimal) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT frozen FROM wallets WHERE wallet_id = %s FOR UPDATE",
                (wallet_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise WalletNotFoundError(wallet_id)
            if row[0]:
                raise WalletFrozenError(wallet_id)
            cur.execute(
                "UPDATE wallets SET balance = balance + %s, updated_at = now() "
                "WHERE wallet_id = %s",
                (amount, wallet_id),
            )

    def total_supply(self) -> Decimal:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(balance), 0) FROM wallets")
            row = cur.fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def list_all_wallets(self) -> list[WalletAdminRecord]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.wallet_id, w.user_id, u.username, u.display_name,
                       w.currency, w.balance, w.public_key, w.frozen
                FROM wallets w
                JOIN users u ON u.user_id = w.user_id
                ORDER BY u.username, w.created_at
                """
            )
            rows = cur.fetchall()
        return [
            WalletAdminRecord(
                wallet_id=r[0],
                user_id=r[1],
                username=r[2],
                display_name=r[3],
                currency=r[4],
                balance=Decimal(r[5]),
                public_key=r[6],
                frozen=bool(r[7]),
            )
            for r in rows
        ]


_WALLET_SELECT = (
    "SELECT wallet_id, user_id, currency, balance, public_key, frozen FROM wallets"
)


def _row_to_wallet(row: tuple | None) -> WalletRecord | None:
    if row is None:
        return None
    return WalletRecord(
        wallet_id=row[0],
        user_id=row[1],
        currency=row[2],
        balance=Decimal(row[3]),
        public_key=row[4],
        frozen=bool(row[5]),
    )
