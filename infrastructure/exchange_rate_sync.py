"""External exchange-rate feed sync (MC-3+)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from urllib.parse import urlparse
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
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ExchangeRateSyncError("Only https scheme is allowed for exchange feeds")
    with request.urlopen(url, timeout=10) as resp:  # nosec B310
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


def fetch_crypto_com_rate(pair: ExchangeRateSyncPair) -> Decimal:
    """Fetch exchange rate from Crypto.com API.
    
    Crypto.com v2 Ticker API endpoint returns price for a given instrument_name.
    Example: BTC_USDT, ETH_USDT, etc.
    """
    symbol = pair.symbol or f"{pair.from_currency}_{pair.to_currency}"
    url = f"https://api.crypto.com/v2/public/get-ticker?instrument_name={symbol}"
    try:
        data = _fetch_json(url)
    except Exception as exc:  # noqa: BLE001
        raise ExchangeRateSyncError(
            f"Crypto.com request failed for {symbol}: {exc}"
        ) from exc

    # Crypto.com v2 API wraps result in 'result' key
    result = data.get("result", {})
    if not result:
        raise ExchangeRateSyncError(
            f"Crypto.com response empty or error: {data.get('code', 'UNKNOWN')}"
        )

    # Get the instrument data (first item in the result list)
    instruments = result.get("data", [])
    if not instruments:
        raise ExchangeRateSyncError(f"No instruments in Crypto.com response for {symbol}")

    instrument = instruments[0]
    # Crypto.com returns 'h' (high), 'l' (low), 'a' (ask), 'b' (bid)
    # Use the mark price (mid of bid/ask) or close price if available
    price_str = instrument.get("a") or instrument.get("b")

    if price_str is None:
        raise ExchangeRateSyncError(f"Crypto.com response missing price for {symbol}")

    try:
        return Decimal(str(price_str))
    except Exception as exc:  # noqa: BLE001
        raise ExchangeRateSyncError(
            f"Invalid price for {symbol}: {price_str}"
        ) from exc


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
        elif provider == PROVIDER_CRYPTO_COM:
            rate = fetch_crypto_com_rate(pair)
        else:
            raise ExchangeRateSyncError(f"Unsupported provider: {provider}")

        record = currencies.set_exchange_rate(
            from_currency=pair.from_currency,
            to_currency=pair.to_currency,
            rate=rate,
            fee_rate=Decimal("0"),
            source=provider,
        )
        results.append(record)

    return results
