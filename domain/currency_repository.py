"""Currency and exchange-rate persistence contracts (MC-1..MC-3)."""

from __future__ import annotations

from dataclasses import dataclass
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
    ) -> ExchangeRateRecord: ...


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
    ) -> ExchangeRateRecord:
        self._rate_seq += 1
        record = ExchangeRateRecord(
            rate_id=self._rate_seq,
            from_currency=from_currency,
            to_currency=to_currency,
            rate=rate,
            fee_rate=fee_rate,
            updated_at="now",
        )
        self._rates.append(record)
        return record
