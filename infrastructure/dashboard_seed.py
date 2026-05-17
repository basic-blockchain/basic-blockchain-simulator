"""Bootstrap-seed the dashboard's currencies catalog and X/USDT rates.

Phase 6i.1 — the simulator boots with an empty FX table by default,
which collapsed every USD-equivalent aggregate on the admin dashboard
to zero (Screenshots_452 / _454). This module owns the one-shot seed
that runs from `create_app()`:

  1. Ensure the quote currency (USDT by default) and a handful of
     common assets exist in `currencies`.
  2. If no rates exist for `<asset>/<quote>` pairs, seed a sensible
     mid-market default per asset.

The seed is **idempotent** at both levels:
  - `create_currency(...)` re-raises `CurrencyAlreadyExistsError` which
    we catch and skip.
  - Rate inserts happen only when `list_exchange_rates(...)` for the
    pair returns empty.

Operators that prefer to drive their own catalog can disable the
whole pass via `DASHBOARD_BOOTSTRAP_SEED=false` and seed manually
through `/admin/currencies` + `/admin/exchange-rates`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from domain.currency_repository import (
    CurrencyAlreadyExistsError,
    CurrencyRepositoryProtocol,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _SeedCurrency:
    code: str
    name: str
    decimals: int


@dataclass(frozen=True, slots=True)
class _SeedRate:
    from_currency: str
    rate: Decimal


# Default catalog covering the simulator's NATIVE token plus the four
# most common assets users actually trade in the simulator's mempool
# (BTC / ETH / SOL) plus the second-stablecoin USDC. Add to this list
# as new currencies become common — keep `decimals` aligned with
# real-world precision so `set_exchange_rate` round-trips cleanly.
_DEFAULT_CURRENCIES: tuple[_SeedCurrency, ...] = (
    _SeedCurrency("USDT",   "Tether USD",   2),
    _SeedCurrency("USDC",   "USD Coin",     2),
    _SeedCurrency("BTC",    "Bitcoin",      8),
    _SeedCurrency("ETH",    "Ethereum",     8),
    _SeedCurrency("SOL",    "Solana",       6),
    _SeedCurrency("NATIVE", "Native",       8),
)


# Mid-market reference rates against USDT. These are intentionally
# "stale 2025 average" numbers — operators who want live data should
# either disable the seed and call `/admin/exchange-rates/sync` or
# override individual rates via the admin endpoint after boot.
_DEFAULT_RATES: tuple[_SeedRate, ...] = (
    _SeedRate("USDC",   Decimal("1.00")),
    _SeedRate("BTC",    Decimal("60000")),
    _SeedRate("ETH",    Decimal("3000")),
    _SeedRate("SOL",    Decimal("150")),
    _SeedRate("NATIVE", Decimal("0.5")),
)


def bootstrap_dashboard_seed(
    *,
    currencies: CurrencyRepositoryProtocol,
    quote_currency: str = "USDT",
) -> None:
    """Idempotent seed of currencies + X/<quote> exchange rates.

    Safe to call on every boot — existing currencies and existing
    rates are detected and skipped. Logs a single info line per
    inserted row so the boot log stays readable.
    """
    seeded_currencies = 0
    for spec in _DEFAULT_CURRENCIES:
        if currencies.get_currency(spec.code) is not None:
            continue
        try:
            currencies.create_currency(
                code=spec.code,
                name=spec.name,
                decimals=spec.decimals,
                active=True,
            )
            seeded_currencies += 1
        except CurrencyAlreadyExistsError:
            # Race between two processes both calling the seed — fine
            # to ignore, the other writer won.
            continue

    seeded_rates = 0
    for rate in _DEFAULT_RATES:
        if rate.from_currency == quote_currency:
            # Quote/quote is always 1:1 and not worth storing.
            continue
        existing = currencies.list_exchange_rates(
            from_currency=rate.from_currency,
            to_currency=quote_currency,
            limit=1,
        )
        if existing:
            continue
        currencies.set_exchange_rate(
            from_currency=rate.from_currency,
            to_currency=quote_currency,
            rate=rate.rate,
            fee_rate=Decimal("0"),
            source="BOOTSTRAP_SEED",
        )
        seeded_rates += 1

    if seeded_currencies or seeded_rates:
        logger.info(
            "dashboard_seed",
            extra={
                "data": {
                    "quote_currency": quote_currency,
                    "seeded_currencies": seeded_currencies,
                    "seeded_rates": seeded_rates,
                }
            },
        )
