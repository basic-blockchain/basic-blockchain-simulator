"""Phase I.3 — wallet preview/confirm flow regression tests."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _reload_app(monkeypatch, *, bootstrap_admin: str = "admin"):
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


async def _bearer(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    return {"Authorization": f"Bearer {(await r.get_json())['access_token']}"}


async def test_wallet_preview_then_confirm_creates_wallet_once(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        headers = await _bearer(client, username="alice")

        preview = await client.post(
            "/api/v1/wallets/preview", headers=headers, json={"currency": "NATIVE"}
        )
        preview_body = await preview.get_json()
        assert preview.status_code == 200
        assert preview_body["draft_id"]
        assert len(preview_body["mnemonic"].split()) == 12

        confirm = await client.post(
            "/api/v1/wallets/confirm",
            headers=headers,
            json={"draft_id": preview_body["draft_id"]},
        )
        confirm_body = await confirm.get_json()
        assert confirm.status_code == 201
        assert confirm_body["currency"] == "NATIVE"
        assert confirm_body["wallet_id"].startswith("w_")

        wallets = await client.get("/api/v1/wallets/me", headers=headers)
        wallets_body = await wallets.get_json()
        assert wallets.status_code == 200
        assert wallets_body["count"] == 1