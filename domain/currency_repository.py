"""Currency and exchange-rate persistence contracts (MC-1..MC-3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol


@dataclass(slots=True)
class CurrencyRecord:
    code: str
    name: str
    decimals: int
    active: bool


@dataclass(slots=True)
class ExchangeRateRecord:
    rate_id: int
    from_currency: str
    to_currency: str
    rate: Decimal
    fee_rate: Decimal
    source: str
    updated_at: str


class CurrencyAlreadyExistsError(Exception):
    """Raised when a currency code already exists."""


class CurrencyRepositoryProtocol(Protocol):
    def list_currencies(self, *, active_only: bool = False) -> list[CurrencyRecord]: ...

    def get_currency(self, code: str) -> CurrencyRecord | None: ...

    def create_currency(
        self,
        *,
        code: str,
        name: str,
        decimals: int,
        active: bool = True,
    ) -> None: ...

    def list_exchange_rates(
        self,
        *,
        from_currency: str | None = None,
        to_currency: str | None = None,
        limit: int = 50,
    ) -> list[ExchangeRateRecord]: ...

    def set_exchange_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate: Decimal,
        fee_rate: Decimal,
        source: str,
    ) -> ExchangeRateRecord: ...

    def get_rate_at(
        self,
        *,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> ExchangeRateRecord | None:
        """Latest rate row whose `updated_at <= at`, or `None` when no
        rate exists for the pair as of that point in time. Used by the
        Phase 6e dashboard for the "rate as of confirmed_at" rule
        (BR-AD-06). `at` must be timezone-aware UTC."""
        ...


class InMemoryCurrencyStore:
    def __init__(self) -> None:
        self._currencies: dict[str, CurrencyRecord] = {
            "NATIVE": CurrencyRecord("NATIVE", "Native", 8, True),
        }
        self._rates: list[ExchangeRateRecord] = []
        self._rate_seq = 0

    def list_currencies(self, *, active_only: bool = False) -> list[CurrencyRecord]:
        records = list(self._currencies.values())
        if active_only:
            records = [c for c in records if c.active]
        return sorted(records, key=lambda c: c.code)

    def get_currency(self, code: str) -> CurrencyRecord | None:
        return self._currencies.get(code)

    def create_currency(
        self,
        *,
        code: str,
        name: str,
        decimals: int,
        active: bool = True,
    ) -> None:
        if code in self._currencies:
            raise CurrencyAlreadyExistsError(code)
        self._currencies[code] = CurrencyRecord(code, name, decimals, active)

    def list_exchange_rates(
        self,
        *,
        from_currency: str | None = None,
        to_currency: str | None = None,
        limit: int = 50,
    ) -> list[ExchangeRateRecord]:
        records = self._rates
        if from_currency:
            records = [r for r in records if r.from_currency == from_currency]
        if to_currency:
            records = [r for r in records if r.to_currency == to_currency]
        return list(reversed(records))[:limit]

    def set_exchange_rate(
        self,
        *,
        from_currency: str,
        to_currency: str,
        rate: Decimal,
        fee_rate: Decimal,
        source: str,
    ) -> ExchangeRateRecord:
        self._rate_seq += 1
        record = ExchangeRateRecord(
            rate_id=self._rate_seq,
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            fee_rate=fee_rate,
            source=source,
            # ISO8601 UTC so `get_rate_at` can compare against any
            # confirmed_at timestamp from the chain.
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._rates.append(record)
        return record

    def get_rate_at(
        self,
        *,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> ExchangeRateRecord | None:
        candidates = [
            r for r in self._rates
            if r.from_currency == from_currency
            and r.to_currency == to_currency
            and _isoparse(r.updated_at) <= at
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: _isoparse(r.updated_at))


def _isoparse(value: str) -> datetime:
    """Parse an ISO8601 timestamp the in-memory store wrote (or a
    PG-shaped string). Naive values are assumed UTC."""
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts
