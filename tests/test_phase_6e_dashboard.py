"""Phase 6e — dashboard endpoint tests.

Covers the four contracts pinned in `docs/api-reference.md` §
"Admin dashboard endpoints (Phase 6e — proposed contracts)" plus the
business rules in `docs/business-rules.md` § 8d:

- GET /admin/audit `?severity=` / `?since=` filters and the
  server-derived `severity` field on every entry (BR-AD-10/11).
- GET /admin/stats `?compare=` delta block (BR-AD-09).
- GET /admin/volume time-bucketed USD totals (BR-AD-06/07/08).
- GET /admin/movements/top USD-ranked transfers (BR-AD-12).

This module owns its own bootstrap helpers — the older `test_rbac.py`
helpers are inlined here rather than re-exported, keeping the new
phase isolated from any future refactor of the legacy file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _register_activate(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title(), "email": f"{username}@x.com"},
    )
    body = await r.get_json()
    assert r.status_code == 201, body
    code = body["activation_code"]
    r = await client.post(
        "/api/v1/auth/activate",
        json={"username": username, "activation_code": code, "password": password},
    )
    assert r.status_code == 200
    return body["user_id"]


async def _login(client, *, username: str, password: str = "hunter12345") -> str:
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    )
    assert r.status_code == 200, await r.get_json()
    return (await r.get_json())["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bootstrap_admin(monkeypatch):
    """Boot the app with an auto-promoted admin (alice). Returns the
    test-client context manager and the admin token. Caller must enter
    the context manager and use the token to call /admin/* routes."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    return module


# ── /admin/audit · severity field (BR-AD-10) ────────────────────────────


async def test_audit_entries_carry_severity(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            bob_id = await _register_activate(client, username="bob")
            token = await _login(client, username="alice")
            # Trigger one critical (ban) and one info (unban) event.
            await client.post(f"/api/v1/admin/users/{bob_id}/ban", headers=_auth(token))
            await client.post(f"/api/v1/admin/users/{bob_id}/unban", headers=_auth(token))

            r = await client.get("/api/v1/admin/audit", headers=_auth(token))
            body = await r.get_json()
            assert r.status_code == 200
            by_action = {e["action"]: e for e in body["entries"]}
            assert by_action["USER_BANNED"]["severity"] == "critical"
            assert by_action["USER_UNBANNED"]["severity"] == "info"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_audit_severity_filter_drops_other_levels(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            bob_id = await _register_activate(client, username="bob")
            token = await _login(client, username="alice")
            await client.post(f"/api/v1/admin/users/{bob_id}/ban", headers=_auth(token))
            await client.post(f"/api/v1/admin/users/{bob_id}/unban", headers=_auth(token))
            await client.post(
                f"/api/v1/admin/users/{bob_id}/roles",
                headers=_auth(token),
                json={"action": "grant", "role": "OPERATOR"},
            )

            r = await client.get(
                "/api/v1/admin/audit?severity=critical", headers=_auth(token),
            )
            body = await r.get_json()
            assert r.status_code == 200
            assert all(e["severity"] == "critical" for e in body["entries"])
            assert {e["action"] for e in body["entries"]} == {"USER_BANNED"}
            assert body["filters"]["severity"] == "critical"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_audit_rejects_invalid_severity(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/audit?severity=panic", headers=_auth(token),
            )
            assert r.status_code == 400
            assert (await r.get_json())["code"] == "SEVERITY_INVALID"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


# ── /admin/stats ?compare= (BR-AD-09) ──────────────────────────────────


async def test_stats_compare_appends_delta_block(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            await _register_activate(client, username="bob")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/stats?compare=7d", headers=_auth(token),
            )
            assert r.status_code == 200
            body = await r.get_json()
            # Unchanged shape
            assert body["users"]["total"] >= 2
            # Compare block present
            cmp = body["compare"]
            assert cmp["range"] == "7d"
            assert "previous_period_end" in cmp
            users_total = cmp["users"]["total"]
            assert {"current", "previous", "delta_abs", "delta_pct"} <= users_total.keys()
            assert users_total["current"] == body["users"]["total"]
            # previous is 0 (everyone signed up just now) → delta_pct null
            assert users_total["previous"] == 0
            assert users_total["delta_pct"] is None
            assert users_total["delta_abs"] == users_total["current"]
            # transactions block exists; no chain activity → both 0
            assert cmp["transactions"]["count"]["current"] == 0
            assert cmp["transactions"]["count"]["previous"] == 0
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_stats_rejects_invalid_compare(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/stats?compare=99d", headers=_auth(token),
            )
            assert r.status_code == 400
            assert (await r.get_json())["code"] == "COMPARE_INVALID"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_stats_without_compare_unchanged_shape(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get("/api/v1/admin/stats", headers=_auth(token))
            assert r.status_code == 200
            body = await r.get_json()
            assert "compare" not in body
            assert {"users", "wallets", "balances"} <= body.keys()
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


# ── /admin/volume (BR-AD-06/07/08) ─────────────────────────────────────


async def test_volume_rejects_invalid_range(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get("/api/v1/admin/volume?range=2y", headers=_auth(token))
            assert r.status_code == 400
            assert (await r.get_json())["code"] == "RANGE_INVALID"
            # Missing range also fails.
            r = await client.get("/api/v1/admin/volume", headers=_auth(token))
            assert r.status_code == 400
            assert (await r.get_json())["code"] == "RANGE_INVALID"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_volume_empty_chain_returns_grid_with_zeros(monkeypatch):
    """A freshly-booted simulator has the genesis block only (no
    transactions). /admin/volume must still return a continuous bucket
    grid covering the requested window — every bucket at zero — so the
    frontend can render the axis without client-side back-filling."""
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/volume?range=30d", headers=_auth(token),
            )
            assert r.status_code == 200
            body = await r.get_json()
            assert body["range"] == "30d"
            assert body["bucket"] == "day"
            assert body["currency"] == "USD"
            # 30 day-buckets at zero (no tx) covers the window.
            assert len(body["series"]) >= 30
            assert all(row["tx_count"] == 0 for row in body["series"])
            assert all(row["volume_usd"] == "0" for row in body["series"])
            assert body["totals"] == {
                "volume_usd": "0", "tx_count": 0, "unpriced_count": 0,
            }
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_volume_week_bucket_default_for_90d(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/volume?range=90d", headers=_auth(token),
            )
            assert r.status_code == 200
            body = await r.get_json()
            assert body["bucket"] == "week"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


# ── /admin/movements/top (BR-AD-12) ────────────────────────────────────


async def test_movements_top_rejects_invalid_range(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/movements/top?range=2y", headers=_auth(token),
            )
            assert r.status_code == 400
            assert (await r.get_json())["code"] == "RANGE_INVALID"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_movements_top_empty_chain_returns_empty_list(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get(
                "/api/v1/admin/movements/top", headers=_auth(token),
            )
            assert r.status_code == 200
            body = await r.get_json()
            assert body["range"] == "24h"
            assert body["limit"] == 10
            assert body["count"] == 0
            assert body["movements"] == []
            assert body["total_volume_usd"] == "0"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_movements_top_clamps_limit(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            # Limit > 50 is clamped to 50.
            r = await client.get(
                "/api/v1/admin/movements/top?limit=500", headers=_auth(token),
            )
            assert r.status_code == 200
            assert (await r.get_json())["limit"] == 50
            # limit=0 is clamped to 1.
            r = await client.get(
                "/api/v1/admin/movements/top?limit=0", headers=_auth(token),
            )
            assert (await r.get_json())["limit"] == 1
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


# ── Helper-level coverage (no HTTP) ────────────────────────────────────


def test_convert_to_usd_uses_rate_at_timestamp():
    """`_convert_to_usd` resolves the rate via `get_rate_at(at)`, not
    the latest row — historical charts must not retroactively shift
    (BR-AD-06)."""
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from domain.currency_repository import InMemoryCurrencyStore
    from api.admin_routes import _convert_to_usd

    store = InMemoryCurrencyStore()
    store.create_currency(code="USD", name="US Dollar", decimals=2)
    store.create_currency(code="BTC", name="Bitcoin", decimals=8)

    long_ago = datetime.now(timezone.utc) - timedelta(days=365)
    # Seed two rates back-to-back. We then take "future" snapshots to
    # confirm the lookup picks the latest <= at and returns None when
    # `at` predates every row.
    store.set_exchange_rate(
        from_currency="BTC", to_currency="USD",
        rate=Decimal("10"), fee_rate=Decimal("0"), source="seed",
    )
    store.set_exchange_rate(
        from_currency="BTC", to_currency="USD",
        rate=Decimal("20"), fee_rate=Decimal("0"), source="seed",
    )

    # `long_ago` predates every insert — no rate.
    assert _convert_to_usd(
        currencies=store, from_currency="BTC", amount=Decimal("1"), at=long_ago,
    ) is None
    # A "well after the inserts" lookup hits the latest rate ($20).
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert _convert_to_usd(
        currencies=store, from_currency="BTC", amount=Decimal("1"), at=future,
    ) == Decimal("20.00")


def test_convert_to_usd_passes_through_when_source_is_usd():
    from datetime import datetime, timezone
    from decimal import Decimal
    from domain.currency_repository import InMemoryCurrencyStore
    from api.admin_routes import _convert_to_usd

    store = InMemoryCurrencyStore()
    now = datetime.now(timezone.utc)
    assert _convert_to_usd(
        currencies=store, from_currency="USD", amount=Decimal("42.5"), at=now,
    ) == Decimal("42.5")


# ── /admin/wallets balance_usd enrichment (Phase 6i) ────────────────────


async def test_admin_wallets_carries_balance_usd_when_rate_exists(monkeypatch):
    """`/admin/wallets` enrichment landed in Phase 6i: every wallet
    gains a `balance_usd` field (null when no FX rate), plus
    aggregate `total_balance_usd` and `unpriced_currencies`."""
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            token = await _login(client, username="alice")
            r = await client.get("/api/v1/admin/wallets", headers=_auth(token))
            assert r.status_code == 200
            body = await r.get_json()
            assert "wallets" in body
            assert "total_balance_usd" in body
            assert "unpriced_currencies" in body
            for w in body["wallets"]:
                # balance_usd is either a string or null — never missing.
                assert "balance_usd" in w
                assert w["balance_usd"] is None or isinstance(w["balance_usd"], str)
            # Fresh boot has only the NATIVE currency seeded with no FX
            # rate; every wallet should be unpriced and total = 0.
            assert body["total_balance_usd"] == "0"
            assert body["unpriced_currencies"] == ["NATIVE"] or all(
                w["balance_usd"] is None for w in body["wallets"]
            )
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)


async def test_audit_since_filter_accepts_24h_window(monkeypatch):
    module = await _bootstrap_admin(monkeypatch)
    try:
        async with module.create_app().test_client() as client:
            await _register_activate(client, username="alice")
            bob_id = await _register_activate(client, username="bob")
            token = await _login(client, username="alice")
            await client.post(f"/api/v1/admin/users/{bob_id}/ban", headers=_auth(token))

            # All events were just produced, so a 24h window must include them.
            r = await client.get(
                "/api/v1/admin/audit?since=24h", headers=_auth(token),
            )
            body = await r.get_json()
            assert r.status_code == 200
            assert body["count"] >= 1
            assert body["filters"]["since"] == "24h"

            # Bogus token returns VALIDATION_ERROR (not SEVERITY_INVALID).
            bad = await client.get(
                "/api/v1/admin/audit?since=1week", headers=_auth(token),
            )
            assert bad.status_code == 400
            assert (await bad.get_json())["code"] == "VALIDATION_ERROR"
    finally:
        import importlib
        import config

        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
        importlib.reload(config)
