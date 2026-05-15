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
    management, wallet management ops (VIEW_WALLETS, FREEZE_WALLET,
    UNFREEZE_WALLET), and the admin's own wallet ops. MINT and
    VIEW_TRANSFERS remain absent — they require an explicit grant via
    `user_permissions`."""
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
        # Wallet management ops added in Phase I.5
        Permission.VIEW_WALLETS,
        Permission.FREEZE_WALLET,
        Permission.UNFREEZE_WALLET,
    }
    not_granted_by_default = {
        Permission.MINT,
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
        for u in listing["users"]:
            assert "deleted_at" in u
            assert u["deleted_at"] is None

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


# ── Audit log filtering (Gap #20) ───────────────────────────────────────


def _seed_audit_store():
    """Build an InMemoryUserStore with a known set of audit entries so the
    filter tests can assert exact slices without spinning up the HTTP app."""
    from domain.user_repository import InMemoryUserStore

    store = InMemoryUserStore()
    store.append_audit(actor_id="alice", action="USER_BANNED", target_id="bob", details={})
    store.append_audit(actor_id="alice", action="USER_UNBANNED", target_id="bob", details={})
    store.append_audit(actor_id="alice", action="USER_BANNED", target_id="carol", details={})
    store.append_audit(actor_id="dave", action="USER_BANNED", target_id="bob", details={})
    store.append_audit(actor_id="dave", action="ROLE_GRANTED", target_id="carol", details={"role": "OPERATOR"})
    return store


def test_recent_audit_unfiltered_returns_all_newest_first():
    store = _seed_audit_store()
    entries = store.recent_audit()
    assert [e.action for e in entries] == [
        "ROLE_GRANTED",
        "USER_BANNED",
        "USER_BANNED",
        "USER_UNBANNED",
        "USER_BANNED",
    ]


def test_recent_audit_filters_by_action():
    store = _seed_audit_store()
    entries = store.recent_audit(action="USER_BANNED")
    assert len(entries) == 3
    assert all(e.action == "USER_BANNED" for e in entries)


def test_recent_audit_filters_by_actor_id():
    store = _seed_audit_store()
    entries = store.recent_audit(actor_id="dave")
    assert len(entries) == 2
    assert all(e.actor_id == "dave" for e in entries)


def test_recent_audit_filters_by_target_id():
    store = _seed_audit_store()
    entries = store.recent_audit(target_id="carol")
    assert len(entries) == 2
    assert all(e.target_id == "carol" for e in entries)


def test_recent_audit_combined_filters_use_and_semantics():
    store = _seed_audit_store()
    entries = store.recent_audit(
        action="USER_BANNED", actor_id="alice", target_id="bob"
    )
    assert len(entries) == 1
    assert entries[0].actor_id == "alice"
    assert entries[0].action == "USER_BANNED"
    assert entries[0].target_id == "bob"


def test_recent_audit_combined_filters_narrow_to_empty_when_no_match():
    store = _seed_audit_store()
    entries = store.recent_audit(action="USER_BANNED", actor_id="dave", target_id="carol")
    assert entries == []


# ── Role-level permission overrides (Gap #16) ───────────────────────────


async def test_admin_can_list_role_permissions(monkeypatch):
    """`GET /admin/roles` returns the effective permission set for every
    known role. With no overrides the listing matches `ROLE_PERMISSIONS`."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        token, _ = await _login_and_token(client, username="alice")
        r = await client.get(
            "/api/v1/admin/roles", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        body = await r.get_json()
        roles = body["roles"]
        for role_name, baseline in ROLE_PERMISSIONS.items():
            assert role_name in roles
            assert sorted(baseline) == roles[role_name]


async def test_admin_can_grant_and_revoke_role_permission(monkeypatch):
    """Granting VIEW_USERS to VIEWER appears in the role listing; revoking
    it removes the override (effective set returns to the baseline)."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        token, _ = await _login_and_token(client, username="alice")

        # Grant
        r = await client.post(
            "/api/v1/admin/roles/VIEWER/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "permission": "VIEW_USERS"},
        )
        assert r.status_code == 200, await r.get_json()
        body = await r.get_json()
        assert body["role"] == "VIEWER"
        assert body["action"] == "ROLE_PERMISSION_GRANTED"
        assert "VIEW_USERS" in body["permissions"]

        # GET /admin/roles reflects the override
        r = await client.get(
            "/api/v1/admin/roles", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        roles = (await r.get_json())["roles"]
        assert "VIEW_USERS" in roles["VIEWER"]

        # Revoke
        r = await client.post(
            "/api/v1/admin/roles/VIEWER/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "revoke", "permission": "VIEW_USERS"},
        )
        assert r.status_code == 200
        body = await r.get_json()
        assert body["action"] == "ROLE_PERMISSION_REVOKED"
        assert "VIEW_USERS" not in body["permissions"]


async def test_role_permission_endpoint_validates_role_and_permission(monkeypatch):
    """Both the role path-parameter and the permission body field are
    validated against the known sets; unknown values yield 400 with code
    VALIDATION_ERROR."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        token, _ = await _login_and_token(client, username="alice")

        # Unknown role
        r = await client.post(
            "/api/v1/admin/roles/WIZARD/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "permission": "VIEW_USERS"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"

        # Unknown permission
        r = await client.post(
            "/api/v1/admin/roles/VIEWER/permissions",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "grant", "permission": "WALK_THE_DOG"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"
# ── Audit log filtering (Gap #20) ───────────────────────────────────────


def _seed_audit_store():
    """Build an InMemoryUserStore with a known set of audit entries so the
    filter tests can assert exact slices without spinning up the HTTP app."""
    from domain.user_repository import InMemoryUserStore

    store = InMemoryUserStore()
    store.append_audit(actor_id="alice", action="USER_BANNED", target_id="bob", details={})
    store.append_audit(actor_id="alice", action="USER_UNBANNED", target_id="bob", details={})
    store.append_audit(actor_id="alice", action="USER_BANNED", target_id="carol", details={})
    store.append_audit(actor_id="dave", action="USER_BANNED", target_id="bob", details={})
    store.append_audit(actor_id="dave", action="ROLE_GRANTED", target_id="carol", details={"role": "OPERATOR"})
    return store


def test_recent_audit_unfiltered_returns_all_newest_first():
    store = _seed_audit_store()
    entries = store.recent_audit()
    assert [e.action for e in entries] == [
        "ROLE_GRANTED",
        "USER_BANNED",
        "USER_BANNED",
        "USER_UNBANNED",
        "USER_BANNED",
    ]


def test_recent_audit_filters_by_action():
    store = _seed_audit_store()
    entries = store.recent_audit(action="USER_BANNED")
    assert len(entries) == 3
    assert all(e.action == "USER_BANNED" for e in entries)


def test_recent_audit_filters_by_actor_id():
    store = _seed_audit_store()
    entries = store.recent_audit(actor_id="dave")
    assert len(entries) == 2
    assert all(e.actor_id == "dave" for e in entries)


def test_recent_audit_filters_by_target_id():
    store = _seed_audit_store()
    entries = store.recent_audit(target_id="carol")
    assert len(entries) == 2
    assert all(e.target_id == "carol" for e in entries)


def test_recent_audit_combined_filters_use_and_semantics():
    store = _seed_audit_store()
    entries = store.recent_audit(
        action="USER_BANNED", actor_id="alice", target_id="bob"
    )
    assert len(entries) == 1
    assert entries[0].actor_id == "alice"
    assert entries[0].action == "USER_BANNED"
    assert entries[0].target_id == "bob"


def test_recent_audit_combined_filters_narrow_to_empty_when_no_match():
    store = _seed_audit_store()
    entries = store.recent_audit(action="USER_BANNED", actor_id="dave", target_id="carol")
    assert entries == []


# ── GET /admin/stats ────────────────────────────────────────────────────


async def test_admin_stats_returns_correct_aggregates(monkeypatch):
    """GET /admin/stats returns user counts, wallet counts, and balances."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="alice")
        await _register_activate(client, username="bob")
        token, _ = await _login_and_token(client, username="alice")

        r = await client.get(
            "/api/v1/admin/stats", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        body = await r.get_json()

        assert "users" in body
        assert "wallets" in body
        assert "balances" in body

        # alice + bob registered, neither banned nor deleted
        assert body["users"]["total"] >= 2
        assert body["users"]["active"] >= 2
        assert body["users"]["banned"] == 0
        assert body["users"]["deleted"] == 0

        # wallet counts are non-negative integers
        assert body["wallets"]["total"] >= 0
        assert body["wallets"]["frozen"] >= 0


async def test_admin_stats_requires_authentication():
    """GET /admin/stats returns 401 when called without a token."""
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.get("/api/v1/admin/stats")
        assert r.status_code == 401


async def test_non_admin_cannot_access_stats(monkeypatch):
    """GET /admin/stats returns 403 for a VIEWER user."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="viewer")
        token, _ = await _login_and_token(client, username="viewer")
        r = await client.get(
            "/api/v1/admin/stats", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403
