"""PostgreSQL adapter for currencies and exchange rates (MC-1..MC-3)."""

from __future__ import annotations

from datetime import datetime
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
        params: list[object] = []
        if from_currency and to_currency:
            query = (
                "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
                "FROM exchange_rates WHERE from_currency = %s AND to_currency = %s "
                "ORDER BY updated_at DESC LIMIT %s"
            )
            params.extend([from_currency, to_currency, limit])
        elif from_currency:
            query = (
                "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
                "FROM exchange_rates WHERE from_currency = %s ORDER BY updated_at DESC LIMIT %s"
            )
            params.extend([from_currency, limit])
        elif to_currency:
            query = (
                "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
                "FROM exchange_rates WHERE to_currency = %s ORDER BY updated_at DESC LIMIT %s"
            )
            params.extend([to_currency, limit])
        else:
            query = (
                "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
                "FROM exchange_rates ORDER BY updated_at DESC LIMIT %s"
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

    def get_rate_at(
        self,
        *,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> ExchangeRateRecord | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT rate_id, from_currency, to_currency, rate, fee_rate, source, updated_at "
                "FROM exchange_rates "
                "WHERE from_currency = %s AND to_currency = %s AND updated_at <= %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (from_currency, to_currency, at),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return ExchangeRateRecord(
            rate_id=int(row[0]),
            from_currency=row[1],
            to_currency=row[2],
            rate=Decimal(row[3]),
            fee_rate=Decimal(row[4]),
            source=row[5],
            updated_at=str(row[6]),
        )
