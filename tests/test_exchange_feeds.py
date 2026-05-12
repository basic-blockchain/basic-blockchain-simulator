"""Exchange rate feed sync — Binance and Crypto.com integration."""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest

from infrastructure.exchange_rate_sync import (
    ExchangeRateSyncError,
    ExchangeRateSyncPair,
    PROVIDER_BINANCE,
    PROVIDER_CRYPTO_COM,
    fetch_binance_rate,
    fetch_crypto_com_rate,
    sync_exchange_rates,
)


# ── Binance API ──────────────────────────────────────────────────────────────


def test_fetch_binance_rate_success():
    """Fetch rate from Binance API."""
    pair = ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {"price": "80700.50"}
        rate = fetch_binance_rate(pair)
        assert rate == Decimal("80700.50")
        mock_fetch.assert_called_once_with(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
        )


def test_fetch_binance_rate_missing_price():
    """Binance response missing price field."""
    pair = ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {}
        with pytest.raises(ExchangeRateSyncError, match="missing price"):
            fetch_binance_rate(pair)


def test_fetch_binance_rate_custom_symbol():
    """Use custom symbol for Binance pair."""
    pair = ExchangeRateSyncPair(
        from_currency="BTC", to_currency="USDT", symbol="BTCBUSD"
    )
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {"price": "79000"}
        rate = fetch_binance_rate(pair)
        assert rate == Decimal("79000")
        mock_fetch.assert_called_once_with(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCBUSD"
        )


# ── Crypto.com API ────────────────────────────────────────────────────────────


def test_fetch_crypto_com_rate_success():
    """Fetch rate from Crypto.com v2 API."""
    pair = ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {
            "result": {
                "data": [
                    {
                        "a": "80700.38",  # ask price
                        "b": "80699.38",  # bid price
                    }
                ]
            }
        }
        rate = fetch_crypto_com_rate(pair)
        assert rate == Decimal("80700.38")  # Uses ask price
        mock_fetch.assert_called_once_with(
            "https://api.crypto.com/v2/public/get-ticker?instrument_name=BTC_USDT"
        )


def test_fetch_crypto_com_rate_fallback_bid():
    """Crypto.com fallback to bid price if ask missing."""
    pair = ExchangeRateSyncPair(from_currency="ETH", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {
            "result": {
                "data": [
                    {
                        "b": "2283.55",  # bid price only
                    }
                ]
            }
        }
        rate = fetch_crypto_com_rate(pair)
        assert rate == Decimal("2283.55")


def test_fetch_crypto_com_rate_custom_symbol():
    """Use custom symbol for Crypto.com pair."""
    pair = ExchangeRateSyncPair(
        from_currency="BTC", to_currency="USDT", symbol="BTC_USDC"
    )
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {
            "result": {"data": [{"a": "80500"}]}
        }
        rate = fetch_crypto_com_rate(pair)
        assert rate == Decimal("80500")
        mock_fetch.assert_called_once_with(
            "https://api.crypto.com/v2/public/get-ticker?instrument_name=BTC_USDC"
        )


def test_fetch_crypto_com_rate_empty_response():
    """Crypto.com returns empty result."""
    pair = ExchangeRateSyncPair(from_currency="XYZ", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {"result": {}}
        with pytest.raises(ExchangeRateSyncError, match="empty or error"):
            fetch_crypto_com_rate(pair)


def test_fetch_crypto_com_rate_no_data():
    """Crypto.com returns empty data array."""
    pair = ExchangeRateSyncPair(from_currency="XYZ", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {"result": {"data": []}}
        with pytest.raises(ExchangeRateSyncError, match="No instruments"):
            fetch_crypto_com_rate(pair)


def test_fetch_crypto_com_rate_missing_price():
    """Crypto.com data missing both ask and bid."""
    pair = ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.return_value = {
            "result": {
                "data": [
                    {
                        "h": "81000",  # high price, not used
                        "l": "80000",  # low price, not used
                    }
                ]
            }
        }
        with pytest.raises(ExchangeRateSyncError, match="missing price"):
            fetch_crypto_com_rate(pair)


def test_fetch_crypto_com_rate_request_error():
    """Crypto.com request fails (network error)."""
    pair = ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")
    with mock.patch("infrastructure.exchange_rate_sync._fetch_json") as mock_fetch:
        mock_fetch.side_effect = ValueError("Connection refused")
        with pytest.raises(ExchangeRateSyncError, match="request failed"):
            fetch_crypto_com_rate(pair)


# ── Sync orchestration ─────────────────────────────────────────────────────────


def test_sync_exchange_rates_unsupported_provider():
    """Reject unsupported provider."""
    with pytest.raises(ExchangeRateSyncError, match="Unsupported provider"):
        sync_exchange_rates(
            currencies=mock.MagicMock(),
            pairs=[],
            provider="UNKNOWN_PROVIDER",
        )


def test_sync_exchange_rates_binance(currencies_mock):
    """Sync multiple pairs from Binance."""
    pairs = [
        ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT"),
        ExchangeRateSyncPair(from_currency="ETH", to_currency="USDT"),
    ]
    mock_records = [
        mock.MagicMock(
            rate_id=1,
            from_currency="BTC",
            to_currency="USDT",
            rate=Decimal("80700.50"),
            fee_rate=Decimal("0"),
            source="BINANCE",
        ),
        mock.MagicMock(
            rate_id=2,
            from_currency="ETH",
            to_currency="USDT",
            rate=Decimal("2283.50"),
            fee_rate=Decimal("0"),
            source="BINANCE",
        ),
    ]
    currencies_mock.set_exchange_rate.side_effect = mock_records

    with mock.patch("infrastructure.exchange_rate_sync.fetch_binance_rate") as mock_fetch:
        mock_fetch.side_effect = [
            Decimal("80700.50"),
            Decimal("2283.50"),
        ]
        records = sync_exchange_rates(
            currencies=currencies_mock,
            pairs=pairs,
            provider=PROVIDER_BINANCE,
        )

    assert len(records) == 2
    assert records[0].from_currency == "BTC"
    assert records[1].from_currency == "ETH"
    assert currencies_mock.set_exchange_rate.call_count == 2


def test_sync_exchange_rates_crypto_com(currencies_mock):
    """Sync multiple pairs from Crypto.com."""
    pairs = [
        ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT"),
        ExchangeRateSyncPair(from_currency="ETH", to_currency="USDT"),
    ]
    mock_records = [
        mock.MagicMock(
            rate_id=3,
            from_currency="BTC",
            to_currency="USDT",
            rate=Decimal("80700.38"),
            fee_rate=Decimal("0"),
            source="CRYPTO_COM",
        ),
        mock.MagicMock(
            rate_id=4,
            from_currency="ETH",
            to_currency="USDT",
            rate=Decimal("2283.55"),
            fee_rate=Decimal("0"),
            source="CRYPTO_COM",
        ),
    ]
    currencies_mock.set_exchange_rate.side_effect = mock_records

    with mock.patch(
        "infrastructure.exchange_rate_sync.fetch_crypto_com_rate"
    ) as mock_fetch:
        mock_fetch.side_effect = [
            Decimal("80700.38"),
            Decimal("2283.55"),
        ]
        records = sync_exchange_rates(
            currencies=currencies_mock,
            pairs=pairs,
            provider=PROVIDER_CRYPTO_COM,
        )

    assert len(records) == 2
    assert records[0].source == "CRYPTO_COM"
    assert records[1].source == "CRYPTO_COM"
    assert currencies_mock.set_exchange_rate.call_count == 2


@pytest.fixture
def currencies_mock():
    """Mock currency repository."""
    return mock.MagicMock()
