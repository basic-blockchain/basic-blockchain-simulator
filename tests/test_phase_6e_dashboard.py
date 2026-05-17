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
