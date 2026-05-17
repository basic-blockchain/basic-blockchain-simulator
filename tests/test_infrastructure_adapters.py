"""Unit tests for infrastructure adapters.

These tests use lightweight fake connections so the PostgreSQL adapter
logic is exercised without requiring a live database.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from domain.audit import ACTION_PERMISSION_GRANTED, ACTION_ROLE_GRANTED, AuditEntry
from domain.models import Block, Transaction
from domain.wallet_repository import WalletAdminRecord, WalletRecord, WalletType
import infrastructure.exchange_rate_sync as exchange_rate_sync_module
import infrastructure.postgres_currency_store as postgres_currency_store_module
import infrastructure.postgres_repository as postgres_repository_module
import infrastructure.postgres_user_store as postgres_user_store_module
import infrastructure.postgres_wallet_store as postgres_wallet_store_module

from infrastructure.exchange_rate_sync import (
    ExchangeRateSyncError,
    ExchangeRateSyncPair,
    PROVIDER_BINANCE,
    PROVIDER_CRYPTO_COM,
    _fetch_json,
    fetch_binance_rate,
    fetch_crypto_com_rate,
    sync_exchange_rates,
)
from infrastructure.postgres_currency_store import PostgresCurrencyStore
from infrastructure.postgres_repository import PostgresBlockRepository
from infrastructure.postgres_user_store import PostgresUserStore
from infrastructure.postgres_wallet_store import PostgresWalletStore


class FakeCursor:
    def __init__(self, *, fetchone_results=None, fetchall_results=None, rowcount=1):
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        self.rowcount = rowcount
        self.executed = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def executemany(self, query, params):
        self.executemany_calls.append((query, list(params)))

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None

    def fetchall(self):
        if self.fetchall_results:
            return self.fetchall_results.pop(0)
        return []


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


class ConnectFactory:
    def __init__(self, cursors):
        self._cursors = iter(cursors)
        self._last_cursor = None

    def __call__(self, dsn):
        try:
            self._last_cursor = next(self._cursors)
        except StopIteration:
            if self._last_cursor is None:
                raise
        return FakeConnection(self._last_cursor)


def _patch_connect(monkeypatch, module, *cursors):
    factory = ConnectFactory(cursors)
    monkeypatch.setattr(module.psycopg2, "connect", factory)
    return factory


def _user_row(user_id="u1", username="alice", display_name="Alice", email="alice@example.com", banned=False, deleted_at=None):
    return (user_id, username, display_name, email, banned, deleted_at)


def _wallet_row(wallet_id="w1", user_id="u1", currency="NATIVE", wallet_type="USER", balance="10", public_key="pk", frozen=False):
    return (wallet_id, user_id, currency, wallet_type, balance, public_key, frozen)


def _block_rows():
    return [
        (1, "2026-05-13T00:00:00Z", 123, "prev", "root", 1, "alice", "bob", "10", None, "sw1", "rw1", 1, "sig1"),
        (1, "2026-05-13T00:00:00Z", 123, "prev", "root", 2, "alice", "bob", "5", "4.75", "sw1", "rw1", 2, "sig2"),
        (2, "2026-05-13T00:01:00Z", 456, "hash1", "root2", None, None, None, None, None, None, None, None, None),
    ]


def test_fetch_json_rejects_non_https():
    with pytest.raises(ExchangeRateSyncError, match="https scheme"):
        _fetch_json("http://example.com/feed")


def test_fetch_binance_and_crypto_com_rates(monkeypatch):
    monkeypatch.setattr(
        exchange_rate_sync_module,
        "_fetch_json",
        lambda url: {"price": "123.45"} if "binance" in url else {"result": {"data": [{"a": "67.89"}]}} ,
    )

    binance = fetch_binance_rate(ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT"))
    crypto = fetch_crypto_com_rate(ExchangeRateSyncPair(from_currency="ETH", to_currency="USDT"))

    assert binance == Decimal("123.45")
    assert crypto == Decimal("67.89")


def test_sync_exchange_rates_with_both_providers(monkeypatch):
    currencies = SimpleNamespace(set_exchange_rate=lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(exchange_rate_sync_module, "fetch_binance_rate", lambda pair: Decimal("10"))
    records = sync_exchange_rates(
        currencies=currencies,
        pairs=[ExchangeRateSyncPair(from_currency="BTC", to_currency="USDT")],
        provider=PROVIDER_BINANCE,
    )
    assert records[0].source == PROVIDER_BINANCE

    monkeypatch.setattr(exchange_rate_sync_module, "fetch_crypto_com_rate", lambda pair: Decimal("11"))
    records = sync_exchange_rates(
        currencies=currencies,
        pairs=[ExchangeRateSyncPair(from_currency="ETH", to_currency="USDT")],
        provider=PROVIDER_CRYPTO_COM,
    )
    assert records[0].source == PROVIDER_CRYPTO_COM


def test_postgres_currency_store_coverage(monkeypatch):
    store = PostgresCurrencyStore("dsn")
    list_cursor = FakeCursor(
        fetchall_results=[
            [("USD", "US Dollar", 2, True), ("EUR", "Euro", 2, False)],
            [(1, "USD", "EUR", "0.92", "0.02", "feed", "2026-05-13")],
            [(2, "USD", "EUR", "0.91", "0.01", "feed2", "2026-05-12")],
            [(3, "EUR", "USD", "1.08", "0.01", "feed3", "2026-05-11")],
            [(4, "USD", "JPY", "150", "0", "feed4", "2026-05-10")],
        ],
        fetchone_results=[("USD", "US Dollar", 2, True), None, (5, "2026-05-13")],
    )
    _patch_connect(monkeypatch, postgres_currency_store_module, list_cursor)

    currencies = store.list_currencies(active_only=True)
    assert currencies[0].code == "USD"
    assert store.get_currency("USD").name == "US Dollar"
    assert store.get_currency("XXX") is None
    assert len(store.list_exchange_rates(from_currency="USD", to_currency="EUR")) == 1


def test_postgres_currency_store_set_exchange_rate(monkeypatch):
    store = PostgresCurrencyStore("dsn")
    cursor = FakeCursor(fetchone_results=[(7, "2026-05-13T00:00:00Z")])
    _patch_connect(monkeypatch, postgres_currency_store_module, cursor)

    record = store.set_exchange_rate(
        from_currency="USD",
        to_currency="EUR",
        rate=Decimal("0.92"),
        fee_rate=Decimal("0.02"),
        source="feed",
    )
    assert record.rate_id == 7
    assert record.rate == Decimal("0.92")


def test_postgres_user_store_core_paths(monkeypatch):
    store = PostgresUserStore("dsn")
    cursor = FakeCursor(
        fetchone_results=[
            (2,),
            ("u1", "hash", "ACTIVATION", "2026-05-13", False),
            ("u1", "hash2", None, "2026-05-13", True),
            ("u1", "hash3", None, "2026-05-13", False),
        ],
        fetchall_results=[
            # get_user_by_id and get_user_by_username now route through
            # `_select_users` which calls `fetchall()`; single-row reads
            # therefore arrive as a one-element list.
            [_user_row()],
            [_user_row()],
            [_user_row(), _user_row(user_id="u2", username="bob", display_name="Bob")],
            [("ADMIN",), ("VIEWER",)],
            [("MINT",), ("VIEW_USERS",)],
            [("ROLE_GRANTED", "x", "u1", "{}", "2026-05-13")],
            [("MINT",), ("VIEW_USERS",)],
            [("ROLE_USER", "VIEW_USERS"), ("ROLE_ADMIN", "MINT")],
            [("PERM_A",), ("PERM_B",)],
            [(1, "actor", ACTION_ROLE_GRANTED, "u1", {"role": "ADMIN"}, "2026-05-13")],
        ],
    )
    _patch_connect(monkeypatch, postgres_user_store_module, cursor)

    store.create_user(user_id="u1", username="alice", display_name="Alice", email="alice@example.com")
    assert store.get_user_by_id("u1").username == "alice"
    assert store.get_user_by_username("alice").display_name == "Alice"
    assert store.count_users() == 2
    assert len(store.list_users()) == 2
    store.set_banned(user_id="u1", banned=True)
    store.soft_delete_user("u1")
    store.restore_user("u1")
    assert cursor.executed


def test_postgres_user_store_permissions_roles_and_audit(monkeypatch):
    store = PostgresUserStore("dsn")
    cursor = FakeCursor(
        fetchone_results=[("u1", "hash", "code", "2026-05-13", False)],
        fetchall_results=[
            [("ADMIN",), ("VIEWER",)],
            [("ADMIN", "PERM_1"), ("ADMIN", "PERM_2")],
            [("PERM_1",), ("PERM_2",)],
            [(1, "actor", ACTION_PERMISSION_GRANTED, "u1", json.dumps({"permission": "MINT"}), "2026-05-13")],
        ],
        rowcount=1,
    )
    _patch_connect(monkeypatch, postgres_user_store_module, cursor)

    store.update_user(user_id="u1", display_name="New", email="new@example.com", username="newname")
    store.create_credentials(user_id="u1", password_hash="hash", activation_code="code")
    assert store.get_credentials("u1").password_hash == "hash"
    store.activate_credentials(user_id="u1", password_hash="hash2")
    store.set_password(user_id="u1", password_hash="hash3", must_change_password=True)
    store.assign_role(user_id="u1", role="ADMIN")
    store.revoke_role(user_id="u1", role="ADMIN")
    assert store.get_roles("u1") == ["ADMIN", "VIEWER"]
    assert store.get_role_overrides()["ADMIN"] == {"PERM_1", "PERM_2"}
    assert store.get_user_overrides("u1") == {"PERM_1", "PERM_2"}
    store.grant_user_permission(user_id="u1", permission="MINT")
    store.revoke_user_permission(user_id="u1", permission="MINT")
    store.grant_role_permission(role="ADMIN", permission="MINT")
    store.revoke_role_permission(role="ADMIN", permission="MINT")
    store.append_audit(actor_id="actor", action="ACTION", target_id="u1", details={"k": "v"})
    audit = store.recent_audit(action="ACTION", actor_id="actor", target_id="u1")
    assert isinstance(audit[0], AuditEntry)


def test_postgres_user_store_falls_back_to_legacy_select(monkeypatch):
    """Pre-V018 databases lack the country/kyc_level/last_active/created_at
    columns; the extended SELECT must drop down to the legacy projection
    instead of bubbling up a 500. The fallback is sticky so the second
    call skips the failing extended query entirely.
    """
    import psycopg2 as _psycopg2

    class FailingThenSucceedingCursor(FakeCursor):
        def __init__(self):
            super().__init__(fetchall_results=[[_user_row()], [_user_row()]])
            self._calls = 0

        def execute(self, query, params=None):  # noqa: D401
            self._calls += 1
            if "country" in query and self._calls == 1:
                raise _psycopg2.errors.UndefinedColumn(
                    'column "country" does not exist'
                )
            super().execute(query, params)

    cursor = FailingThenSucceedingCursor()
    _patch_connect(monkeypatch, postgres_user_store_module, cursor)

    store = PostgresUserStore("dsn")
    # First call: extended SELECT raises, store retries with legacy.
    rec = store.get_user_by_id("u1")
    assert rec is not None
    assert rec.kyc_level == "L0"  # default-when-missing path in _row_to_user
    assert store._users_legacy_only is True
    # Second call: extended SELECT is skipped — only the legacy form runs.
    extended_after = sum(1 for q, _ in cursor.executed if "country" in q)
    assert extended_after == 0, "extended SELECT should not run after fallback latches"
    store.get_user_by_username("alice")
    assert sum(1 for q, _ in cursor.executed if "country" in q) == 0


def test_postgres_user_store_missing_row_paths(monkeypatch):
    store = PostgresUserStore("dsn")
    cursor = FakeCursor(rowcount=0)
    _patch_connect(monkeypatch, postgres_user_store_module, cursor)

    with pytest.raises(KeyError):
        store.soft_delete_user("u-missing")
    with pytest.raises(KeyError):
        store.restore_user("u-missing")
    with pytest.raises(KeyError):
        store.set_password(user_id="u-missing", password_hash="hash")


def test_postgres_wallet_store_core_paths(monkeypatch):
    store = PostgresWalletStore("dsn")
    cursor = FakeCursor(
        fetchone_results=[
            _wallet_row(),
            (Decimal("10"),),
            _wallet_row(wallet_id="w2", user_id="u2", balance="20"),
        ],
        fetchall_results=[
            [_wallet_row(), _wallet_row(wallet_id="w2", user_id="u1", balance="15")],
            [
                ("w1", "u1", "alice", "Alice", "NATIVE", "USER", "10", "pk", False),
            ],
        ],
        rowcount=1,
    )
    _patch_connect(monkeypatch, postgres_wallet_store_module, cursor)

    store.create_wallet(wallet_id="w1", user_id="u1", public_key="pk")
    assert store.get_wallet("w1").wallet_id == "w1"
    assert len(store.list_user_wallets("u1")) == 2
    store.set_frozen(wallet_id="w1", frozen=True)
    store.reserve_nonce(wallet_id="w1", nonce=1)
    assert store.total_supply() == Decimal("10")
    assert store.find_wallet_by_type_currency(wallet_type="USER", currency="NATIVE") is not None
    assert store.list_all_wallets()[0].username == "alice"


def test_postgres_wallet_store_transfer_paths(monkeypatch):
    store = PostgresWalletStore("dsn")
    cursor = FakeCursor(
        fetchall_results=[
            [
                ("w1", Decimal("100"), False),
                ("w2", Decimal("20"), False),
            ],
            [
                ("w1", "u1", "USD", "USER", Decimal("100"), "pk1", False),
                ("w2", "u2", "EUR", "USER", Decimal("20"), "pk2", False),
            ],
        ],
        fetchone_results=[(False,), (False,), None],
        rowcount=1,
    )
    _patch_connect(monkeypatch, postgres_wallet_store_module, cursor)

    store.apply_transfer(
        sender_wallet_id="w1",
        receiver_wallet_id="w2",
        amount=Decimal("10"),
        receiver_amount=Decimal("9.50"),
    )
    store.credit(wallet_id="w1", amount=Decimal("5"))


def test_postgres_wallet_store_error_paths(monkeypatch):
    store = PostgresWalletStore("dsn")
    cursor = FakeCursor(fetchone_results=[None], rowcount=0)
    _patch_connect(monkeypatch, postgres_wallet_store_module, cursor)

    with pytest.raises(Exception):
        store.set_frozen(wallet_id="missing", frozen=True)


def test_postgres_block_repository_paths(monkeypatch):
    repo = PostgresBlockRepository("dsn")
    cursor = FakeCursor(fetchall_results=[_block_rows(), _block_rows()], fetchone_results=[(2,)], rowcount=1)
    _patch_connect(monkeypatch, postgres_repository_module, cursor)

    blocks = repo.get_all()
    assert len(blocks) == 2
    assert blocks[0].transactions[1].receiver_amount == Decimal("4.75")
    assert repo.count() == 2
    assert repo.last().index == 1
    repo.replace_all(blocks)
    repo.save_confirmed_transactions(1, blocks[0].transactions)
    confirmed = repo.get_confirmed_transactions()
    assert confirmed == [] or isinstance(confirmed, list)
