"""Phase I.3 — wallet HTTP endpoints (create, list, mint).

Covers `POST /api/v1/wallets`, `GET /api/v1/wallets/me`, and
`POST /api/v1/admin/mint`. Signed transfer flow lives in
`tests/test_transfers.py`; supply conservation in
`tests/test_supply_conservation.py`.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _reload_app(monkeypatch, *, bootstrap_admin: str | None = None):
    """Reload `config` (so env-var-derived constants pick up the test
    override) then load the app module fresh and return it."""
    if bootstrap_admin is None:
        monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    else:
        monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", bootstrap_admin)
    import config

    importlib.reload(config)
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _register_activate(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title()},
    )
    body = await r.get_json()
    code = body["activation_code"]
    await client.post(
        "/api/v1/auth/activate",
        json={"username": username, "activation_code": code, "password": password},
    )
    return body["user_id"]


async def _login_token(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    return (await r.get_json())["access_token"]


async def _bearer(client, *, username: str):
    token = await _login_token(client, username=username)
    return {"Authorization": f"Bearer {token}"}


# ── POST /wallets ───────────────────────────────────────────────────────


async def test_create_wallet_requires_authentication(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        r = await client.post("/api/v1/wallets")
        assert r.status_code == 401


async def test_create_wallet_returns_mnemonic_once(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        h = await _bearer(client, username="alice")
        r = await client.post("/api/v1/wallets", headers=h)
        body = await r.get_json()
        assert r.status_code == 201
        assert body["wallet_id"].startswith("w_")
        assert len(body["mnemonic"].split()) == 12
        assert len(body["public_key"]) == 66  # 33-byte compressed hex
        assert "warning" in body


async def test_get_my_wallets_lists_only_my_own(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        await _register_activate(client, username="bob")
        h_alice = await _bearer(client, username="alice")
        h_bob = await _bearer(client, username="bob")

        await client.post("/api/v1/wallets", headers=h_alice)
        await client.post("/api/v1/wallets", headers=h_alice)
        await client.post("/api/v1/wallets", headers=h_bob)

        r = await client.get("/api/v1/wallets/me", headers=h_alice)
        body = await r.get_json()
        assert r.status_code == 200
        assert body["count"] == 2
        for w in body["wallets"]:
            assert "balance" in w
            assert w["frozen"] is False
            assert w["public_key"]

        r = await client.get("/api/v1/wallets/me", headers=h_bob)
        body = await r.get_json()
        assert body["count"] == 1


# ── POST /admin/mint ────────────────────────────────────────────────────


async def test_admin_mint_requires_explicit_permission_grant(monkeypatch):
    """ADMIN baseline does NOT include MINT (Phase I.2 narrowing). The
    bootstrap admin must grant it to themselves first via
    /admin/users/<self>/permissions."""
    module = _reload_app(monkeypatch, bootstrap_admin="admin")
    async with module.create_app().test_client() as client:
        admin_id = await _register_activate(client, username="admin")
        await _register_activate(client, username="alice")
        h_admin = await _bearer(client, username="admin")
        h_alice = await _bearer(client, username="alice")

        # Alice creates a wallet
        r = await client.post("/api/v1/wallets", headers=h_alice)
        alice_wallet = (await r.get_json())["wallet_id"]

        # Admin tries to mint without MINT permission → 403
        r = await client.post(
            "/api/v1/admin/mint",
            headers=h_admin,
            json={"wallet_id": alice_wallet, "amount": 100},
        )
        assert r.status_code == 403

        # Admin self-grants MINT
        r = await client.post(
            f"/api/v1/admin/users/{admin_id}/permissions",
            headers=h_admin,
            json={"action": "grant", "permission": "MINT"},
        )
        assert r.status_code == 200

        # Now mint succeeds
        r = await client.post(
            "/api/v1/admin/mint",
            headers=h_admin,
            json={"wallet_id": alice_wallet, "amount": 100},
        )
        assert r.status_code == 201

        # Mine to apply
        r = await client.post("/api/v1/mine_block")
        assert r.status_code == 200

        # Alice's wallet now has 100
        r = await client.get("/api/v1/wallets/me", headers=h_alice)
        wallets = (await r.get_json())["wallets"]
        assert wallets[0]["balance"] == 100.0


async def test_admin_mint_rejects_unknown_wallet(monkeypatch):
    module = _reload_app(monkeypatch, bootstrap_admin="admin")
    async with module.create_app().test_client() as client:
        admin_id = await _register_activate(client, username="admin")
        h_admin = await _bearer(client, username="admin")
        # Self-grant MINT first
        await client.post(
            f"/api/v1/admin/users/{admin_id}/permissions",
            headers=h_admin,
            json={"action": "grant", "permission": "MINT"},
        )
        r = await client.post(
            "/api/v1/admin/mint",
            headers=h_admin,
            json={"wallet_id": "w_does_not_exist", "amount": 50},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "WALLET_NOT_FOUND"


async def test_admin_mint_rejects_non_positive_amount(monkeypatch):
    module = _reload_app(monkeypatch, bootstrap_admin="admin")
    async with module.create_app().test_client() as client:
        admin_id = await _register_activate(client, username="admin")
        h_admin = await _bearer(client, username="admin")
        await client.post(
            f"/api/v1/admin/users/{admin_id}/permissions",
            headers=h_admin,
            json={"action": "grant", "permission": "MINT"},
        )
        r = await client.post(
            "/api/v1/admin/mint",
            headers=h_admin,
            json={"wallet_id": "anything", "amount": 0},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_non_admin_cannot_call_mint(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        h = await _bearer(client, username="alice")
        r = await client.post(
            "/api/v1/admin/mint",
            headers=h,
            json={"wallet_id": "anything", "amount": 1},
        )
        assert r.status_code == 403
