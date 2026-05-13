"""PostgreSQL adapter for currencies and exchange rates (MC-1..MC-3)."""

from __future__ import annotations

from decimal import Decimal

import psycopg2

from domain.currency_repository import (
    CurrencyAlreadyExistsError,
    CurrencyRecord,
    ExchangeRateRecord,
)


class PostgresCurrencyStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def list_currencies(self, *, active_only: bool = False) -> list[CurrencyRecord]:
        query = "SELECT code, name, decimals, active FROM currencies"
        params: tuple[object, ...] = ()
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY code"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [CurrencyRecord(code=r[0], name=r[1], decimals=int(r[2]), active=bool(r[3])) for r in rows]

    def get_currency(self, code: str) -> CurrencyRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT code, name, decimals, active FROM currencies WHERE code = %s",
                (code,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return CurrencyRecord(code=row[0], name=row[1], decimals=int(row[2]), active=bool(row[3]))

    def create_currency(
        self,
        *,
        code: str,
        name: str,
        decimals: int,
        active: bool = True,
    ) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO currencies (code, name, decimals, active) VALUES (%s, %s, %s, %s)",
                    (code, name, decimals, active),
                )
        except psycopg2.errors.UniqueViolation as exc:
            raise CurrencyAlreadyExistsError(code) from exc

    def list_exchange_rates(
        self,
        *,
        from_currency: str | None = None,
        to_currency: str | None = None,
        limit: int = 50,
    ) -> list[ExchangeRateRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if from_currency:
            clauses.append("from_currency = %s")
            params.append(from_currency)
        if to_currency:
            clauses.append("to_currency = %s")
            params.append(to_currency)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
            "FROM exchange_rates"
            + where
            + " ORDER BY updated_at DESC LIMIT %s"
        )
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [
            ExchangeRateRecord(
                rate_id=int(r[0]),
                from_currency=r[1],
                to_currency=r[2],
                rate=Decimal(r[3]),
                fee_rate=Decimal(r[4]),
                source=r[5],
                updated_at=str(r[6]),
            )
            for r in rows
        ]

    def set_exchange_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate: Decimal,
        fee_rate: Decimal,
        source: str,
    ) -> ExchangeRateRecord:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO exchange_rates (from_currency, to_currency, rate, fee_rate, source) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING rate_id, updated_at",
                (from_currency, to_currency, rate, fee_rate, source),
            )
            row = cur.fetchone()
        return ExchangeRateRecord(
            rate_id=int(row[0]),
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            fee_rate=fee_rate,
            source=source,
            updated_at=str(row[1]),
        )
