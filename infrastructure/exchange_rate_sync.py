"""External exchange-rate feed sync (MC-3+)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from urllib import request

from domain.currency_repository import CurrencyRepositoryProtocol, ExchangeRateRecord


PROVIDER_BINANCE = "BINANCE"
PROVIDER_CRYPTO_COM = "CRYPTO_COM"


@dataclass(slots=True)
class ExchangeRateSyncPair:
    from_currency: str
    to_currency: str
    symbol: str | None = None


class ExchangeRateSyncError(Exception):
    """Raised when the external feed cannot be fetched or parsed."""


def _fetch_json(url: str) -> dict:
    with request.urlopen(url, timeout=10) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def _binance_symbol(pair: ExchangeRateSyncPair) -> str:
    if pair.symbol:
        return pair.symbol
    return f"{pair.from_currency}{pair.to_currency}"


def fetch_binance_rate(pair: ExchangeRateSyncPair) -> Decimal:
    symbol = _binance_symbol(pair)
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    data = _fetch_json(url)
    price = data.get("price")
    if price is None:
        raise ExchangeRateSyncError(f"Binance response missing price for {symbol}")
    try:
        return Decimal(str(price))
    except Exception as exc:  # noqa: BLE001
        raise ExchangeRateSyncError(f"Invalid price for {symbol}: {price}") from exc


def sync_exchange_rates(
    *,
    currencies: CurrencyRepositoryProtocol,
    pairs: Iterable[ExchangeRateSyncPair],
    provider: str = PROVIDER_BINANCE,
) -> list[ExchangeRateRecord]:
    if provider not in {PROVIDER_BINANCE, PROVIDER_CRYPTO_COM}:
        raise ExchangeRateSyncError(f"Unsupported provider: {provider}")

    results: list[ExchangeRateRecord] = []
    for pair in pairs:
        if provider == PROVIDER_BINANCE:
            rate = fetch_binance_rate(pair)
        else:
            raise ExchangeRateSyncError("Crypto.com feed not implemented yet")

        record = currencies.set_exchange_rate(
            from_currency=pair.from_currency,
            to_currency=pair.to_currency,
            rate=rate,
            fee_rate=Decimal("0"),
            source=provider,
        )
        results.append(record)

    return results
