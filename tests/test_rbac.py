"""Phase I.2 — Roles, permissions, admin endpoints, audit log.

Covers:
- `has_permission` 3-level resolution (default / role override / user
  override) plus the "role override REPLACES baseline" rule.
- `@require_permission` aborts with 401 on no token, 403 on missing
  permission, and lets ADMIN (or any role with the right grant) through.
- The six admin routes: list users, grant/revoke role, ban/unban (with
  the self-ban guard), grant/revoke per-user permission, read audit.
- Audit log captures every state-mutating admin call with the actor,
  action, target, and details payload.
- Banned users cannot log in; the response is the uniform
  AUTH_INVALID_CREDENTIALS to preserve enumeration mitigation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain.auth import Role
from domain.permissions import Permission, ROLE_PERMISSIONS, has_permission


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── Permission resolution unit tests ────────────────────────────────────


def test_admin_baseline_holds_user_management_only():
    """ADMIN's hardcoded baseline covers user / role / permission
    management plus the admin's own wallet ops. Financial-action and
    cross-user data permissions are NOT in the baseline — they require
    an explicit grant via `user_permissions`."""
    granted_by_default = {
        Permission.CREATE_USER,
        Permission.VIEW_USERS,
        Permission.UPDATE_USER,
        Permission.BAN_USER,
        Permission.UNBAN_USER,
        Permission.ASSIGN_ROLE,
        Permission.MANAGE_PERMISSIONS,
        Permission.VIEW_AUDIT_LOG,
        Permission.CREATE_WALLET,
        Permission.TRANSFER,
    }
    not_granted_by_default = {
        Permission.MINT,
        Permission.FREEZE_WALLET,
        Permission.UNFREEZE_WALLET,
        Permission.VIEW_WALLETS,
        Permission.VIEW_TRANSFERS,
    }
    for perm in granted_by_default:
        assert has_permission(
            user_id="u", roles=[Role.ADMIN.value], permission=perm.value
        ), f"ADMIN should hold {perm.value} by default"
    for perm in not_granted_by_default:
        assert not has_permission(
            user_id="u", roles=[Role.ADMIN.value], permission=perm.value
        ), f"ADMIN should NOT hold {perm.value} by default"


def test_admin_can_self_grant_data_permission_via_user_override():
    """An ADMIN can call MANAGE_PERMISSIONS (in the baseline) to grant
    themselves a financial-action permission without changing roles."""
    overrides = {"u": {Permission.MINT.value}}
    assert has_permission(
        user_id="u",
        roles=[Role.ADMIN.value],
        permission=Permission.MINT.value,
        user_overrides=overrides,
    )


def test_operator_can_transfer_but_cannot_ban_user():
    assert has_permission(
        user_id="u", roles=[Role.OPERATOR.value], permission=Permission.TRANSFER.value
    )
    assert not has_permission(
        user_id="u", roles=[Role.OPERATOR.value], permission=Permission.BAN_USER.value
    )


def test_viewer_cannot_mint():
    assert not has_permission(
        user_id="u", roles=[Role.VIEWER.value], permission=Permission.MINT.value
    )


def test_user_override_grants_unprivileged_permission():
    assert has_permission(
        user_id="u",
        roles=[Role.VIEWER.value],
        permission=Permission.MINT.value,
        user_overrides={"u": {Permission.MINT.value}},
    )


def test_role_override_replaces_baseline_when_present():
    # Override ADMIN to a tiny set; the baseline (which had everything)
    # should NOT leak through.
    overrides = {Role.ADMIN.value: {Permission.TRANSFER.value}}
    assert has_permission(
        user_id="u",
        roles=[Role.ADMIN.value],
        permission=Permission.TRANSFER.value,
        role_overrides=overrides,
    )
    assert not has_permission(
        user_id="u",
        roles=[Role.ADMIN.value],
        permission=Permission.BAN_USER.value,
        role_overrides=overrides,
    )


def test_role_override_for_one_role_does_not_affect_other_roles():
    overrides = {Role.OPERATOR.value: set()}  # OPERATOR loses everything
    # User with ADMIN + OPERATOR still gets ADMIN's defaults.
    assert has_permission(
        user_id="u",
        roles=[Role.ADMIN.value, Role.OPERATOR.value],
        permission=Permission.BAN_USER.value,
        role_overrides=overrides,
    )


# ── HTTP — admin endpoints ──────────────────────────────────────────────


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


async def _login_and_token(client, *, username: str, password: str = "hunter12345"):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, await r.get_json()
    body = await r.get_json()
    return body["access_token"], body


async def _bootstrap_admin_and_user(monkeypatch, app_module):
    """Helper: create an ADMIN (alice via BOOTSTRAP_ADMIN_USERNAME) and a
    second OPERATOR-by-default user (bob). Returns the test client plus
    the two access tokens."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    return None  # placeholder, real factory below


async def test_admin_endpoints_require_authentication():
    module = _load_module()
    async with module.create_app().test_client() as client:
        # No token at all — middleware lets it through with current_user=None,
        # the decorator aborts with 401.
        r = await client.get("/api/v1/admin/users")
        assert r.status_code == 401


async def test_non_admin_user_is_forbidden_from_admin_endpoints(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "")  # nobody auto-promotes
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="bob")  # default VIEWER
        token, _ = await _login_and_token(client, username="bob")
        r = await client.get(
            "/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


async def test_admin_can_list_users_and_grant_role(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")  # auto-promoted ADMIN
        bob_id = await _register_activate(client, username="bob")
        token, body = await _login_and_token(client, username="alice")
        assert body["roles"] == [Role.ADMIN.value]

        r = await client.get(
            "/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        listing = await r.get_json()
        assert listing["count"] == 2
        usernames = {u["username"] for u in listing["users"]}
        assert usernames == {"alice", "bob"}

        # Grant OPERATOR to bob
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "role": "OPERATOR"},
        )
        assert r.status_code == 200
        body = await r.get_json()
        assert "OPERATOR" in body["roles"]


async def test_admin_can_revoke_role(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        token, _ = await _login_and_token(client, username="alice")

        # Grant + revoke OPERATOR
        await client.post(
            f"/api/v1/admin/users/{bob_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "role": "OPERATOR"},
        )
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "revoke", "role": "OPERATOR"},
        )
        assert r.status_code == 200
        assert "OPERATOR" not in (await r.get_json())["roles"]


async def test_admin_grant_role_validates_input(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        token, _ = await _login_and_token(client, username="alice")
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "role": "WIZARD"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_admin_can_ban_and_unban(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        token, _ = await _login_and_token(client, username="alice")

        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/ban",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert (await r.get_json())["banned"] is True

        # Banned user cannot log in (uniform AUTH_INVALID_CREDENTIALS)
        r = await client.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "hunter12345"}
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "AUTH_INVALID_CREDENTIALS"

        # Unban
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/unban",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert (await r.get_json())["banned"] is False

        # Login works again
        r = await client.post(
            "/api/v1/auth/login", json={"username": "bob", "password": "hunter12345"}
        )
        assert r.status_code == 200


async def test_admin_cannot_self_ban(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        alice_id = await _register_activate(client, username="alice")
        token, _ = await _login_and_token(client, username="alice")
        r = await client.post(
            f"/api/v1/admin/users/{alice_id}/ban",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "SELF_ACTION_FORBIDDEN"


async def test_user_permission_override_grants_admin_capability(monkeypatch):
    """Granting MANAGE_PERMISSIONS to a VIEWER lets them call the admin
    permission endpoint even though their role does not have it."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token, _ = await _login_and_token(client, username="alice")

        # ADMIN grants MANAGE_PERMISSIONS to bob (who is just VIEWER).
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/permissions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"action": "grant", "permission": "MANAGE_PERMISSIONS"},
        )
        assert r.status_code == 200
        assert "MANAGE_PERMISSIONS" in (await r.get_json())["permissions"]

        # bob can now manage permissions (use the endpoint himself).
        bob_token, _ = await _login_and_token(client, username="bob")
        r = await client.post(
            f"/api/v1/admin/users/{bob_id}/permissions",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"action": "grant", "permission": "VIEW_AUDIT_LOG"},
        )
        assert r.status_code == 200


async def test_audit_log_records_state_mutating_admin_calls(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        alice_id = await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        token, _ = await _login_and_token(client, username="alice")

        # Grant role + ban + unban → 3 audit rows
        await client.post(
            f"/api/v1/admin/users/{bob_id}/roles",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "role": "OPERATOR"},
        )
        await client.post(
            f"/api/v1/admin/users/{bob_id}/ban",
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.post(
            f"/api/v1/admin/users/{bob_id}/unban",
            headers={"Authorization": f"Bearer {token}"},
        )

        r = await client.get(
            "/api/v1/admin/audit", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        body = await r.get_json()
        # Newest first
        actions = [e["action"] for e in body["entries"]]
        assert actions[:3] == ["USER_UNBANNED", "USER_BANNED", "ROLE_GRANTED"]
        for entry in body["entries"][:3]:
            assert entry["actor_id"] == alice_id
            assert entry["target_id"] == bob_id


async def test_admin_endpoints_validate_unknown_user(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        token, _ = await _login_and_token(client, username="alice")
        r = await client.post(
            "/api/v1/admin/users/ghost/ban",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "USER_NOT_FOUND"
