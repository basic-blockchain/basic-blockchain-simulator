"""Phase I.1 — Authentication foundation tests.

Covers:
- Domain primitives (`hash_password`/`verify_password`, JWT round-trip,
  expiry detection, malformed-hash safety).
- HTTP flow (register → activate → login → me) end-to-end against the
  Quart test client with an in-memory user store.
- Authorisation header handling (missing, malformed, tampered, expired).
- Bootstrap-admin promotion: triggers only when `BOOTSTRAP_ADMIN_USERNAME`
  matches the very first user; later registrations of the same username
  collision case is covered by the `USERNAME_TAKEN` 400.
- Account enumeration protection: same response for missing user, wrong
  password, and not-yet-activated account.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import jwt as pyjwt
import pytest

from domain.auth import (
    AuthError,
    Role,
    create_jwt,
    decode_jwt,
    generate_activation_code,
    generate_user_id,
    hash_password,
    verify_password,
)
from domain.user_repository import InMemoryUserStore


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"
TEST_SECRET = "test-secret-with-enough-bytes-for-hs256"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── Domain primitives ────────────────────────────────────────────────────


def test_hash_and_verify_password_roundtrip():
    h = hash_password("hunter12345", rounds=4)
    assert verify_password("hunter12345", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_returns_false_on_malformed_hash():
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_create_and_decode_jwt_roundtrip():
    uid = generate_user_id()
    token = create_jwt(uid, [Role.OPERATOR.value], TEST_SECRET, ttl_seconds=60)
    payload = decode_jwt(token, TEST_SECRET)
    assert payload.sub == uid
    assert payload.roles == ["OPERATOR"]
    assert payload.exp - payload.iat == 60


def test_decode_jwt_rejects_invalid_token():
    with pytest.raises(AuthError) as exc_info:
        decode_jwt("not-a-jwt", TEST_SECRET)
    assert exc_info.value.code == "AUTH_INVALID_TOKEN"


def test_decode_jwt_rejects_expired_token():
    expired = pyjwt.encode(
        {"sub": "u", "roles": ["VIEWER"], "iat": 0, "exp": int(time.time()) - 10},
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError) as exc_info:
        decode_jwt(expired, TEST_SECRET)
    assert exc_info.value.code == "AUTH_EXPIRED_TOKEN"


def test_decode_jwt_rejects_payload_with_missing_claims():
    bad = pyjwt.encode({"foo": "bar"}, TEST_SECRET, algorithm="HS256")
    with pytest.raises(AuthError) as exc_info:
        decode_jwt(bad, TEST_SECRET)
    assert exc_info.value.code == "AUTH_INVALID_TOKEN"


def test_generate_activation_code_uses_safe_alphabet():
    code = generate_activation_code()
    assert len(code) == 16
    assert all(c.isalnum() and (c.isupper() or c.isdigit()) for c in code)


def test_generate_user_id_is_hex_32():
    uid = generate_user_id()
    assert len(uid) == 32
    int(uid, 16)  # raises if not hex


# ── In-memory user store invariants ─────────────────────────────────────


def test_inmemory_store_enforces_unique_username():
    from domain.user_repository import UsernameTakenError

    s = InMemoryUserStore()
    s.create_user(user_id="u1", username="alice", display_name="Alice", email=None)
    with pytest.raises(UsernameTakenError):
        s.create_user(user_id="u2", username="alice", display_name="Other", email=None)


def test_inmemory_store_assign_role_idempotent():
    s = InMemoryUserStore()
    s.create_user(user_id="u1", username="alice", display_name="A", email=None)
    s.assign_role(user_id="u1", role="VIEWER")
    s.assign_role(user_id="u1", role="VIEWER")
    assert s.get_roles("u1") == ["VIEWER"]


# ── HTTP flow ────────────────────────────────────────────────────────────


async def _register_and_activate(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title(), "email": f"{username}@x.com"},
    )
    assert r.status_code == 201, await r.get_json()
    body = await r.get_json()
    code = body["activation_code"]
    r = await client.post(
        "/api/v1/auth/activate",
        json={"username": username, "activation_code": code, "password": password},
    )
    assert r.status_code == 200, await r.get_json()
    return body["user_id"]


async def _login(client, *, username: str, password: str):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return r


async def test_public_routes_remain_public_without_token():
    module = _load_module()
    async with module.create_app().test_client() as client:
        assert (await client.get("/api/v1/chain")).status_code == 200
        assert (await client.get("/api/v1/valid")).status_code == 200
        assert (await client.get("/api/v1/health")).status_code == 200
        assert (await client.get("/api/v1/")).status_code == 200


async def test_register_returns_activation_code_and_user_id():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "display_name": "Alice", "email": "a@x.com"},
        )
        assert r.status_code == 201
        body = await r.get_json()
        assert body["username"] == "alice"
        assert len(body["user_id"]) == 32
        assert len(body["activation_code"]) == 16


async def test_register_rejects_duplicate_username():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await client.post("/api/v1/auth/register", json={"username": "alice", "display_name": "A"})
        r = await client.post("/api/v1/auth/register", json={"username": "alice", "display_name": "B"})
        body = await r.get_json()
        assert r.status_code == 400
        assert body["code"] == "USERNAME_TAKEN"


async def test_register_rejects_missing_username():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.post("/api/v1/auth/register", json={"display_name": "X"})
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_activate_rejects_wrong_code():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await client.post("/api/v1/auth/register", json={"username": "alice", "display_name": "Alice"})
        r = await client.post(
            "/api/v1/auth/activate",
            json={"username": "alice", "activation_code": "WRONG", "password": "hunter12345"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "AUTH_INVALID_ACTIVATION"


async def test_activate_rejects_short_password():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.post("/api/v1/auth/register", json={"username": "alice", "display_name": "Alice"})
        code = (await r.get_json())["activation_code"]
        r = await client.post(
            "/api/v1/auth/activate",
            json={"username": "alice", "activation_code": code, "password": "short"},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_login_before_activation_returns_invalid_credentials():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await client.post("/api/v1/auth/register", json={"username": "alice", "display_name": "Alice"})
        r = await _login(client, username="alice", password="hunter12345")
        body = await r.get_json()
        # Same response code as wrong password — no account enumeration.
        assert r.status_code == 400
        assert body["code"] == "AUTH_INVALID_CREDENTIALS"


async def test_login_with_unknown_user_returns_invalid_credentials():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await _login(client, username="ghost", password="anything")
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "AUTH_INVALID_CREDENTIALS"


async def test_login_with_wrong_password_returns_invalid_credentials():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="totally-wrong")
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "AUTH_INVALID_CREDENTIALS"


async def test_full_flow_register_activate_login_me():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        body = await r.get_json()
        assert r.status_code == 200
        token = body["access_token"]
        assert body["roles"] == ["VIEWER"]

        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        me = await r.get_json()
        assert r.status_code == 200
        assert me["username"] == "alice"
        assert me["roles"] == ["VIEWER"]


async def test_me_without_token_returns_401():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 401


async def test_me_with_tampered_token_returns_401():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        token = (await r.get_json())["access_token"]
        # Flip the last segment to break the signature.
        bad = token[:-5] + ("X" * 5 if not token.endswith("X" * 5) else "Y" * 5)
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401


async def test_me_with_expired_token_returns_401():
    module = _load_module()
    expired = pyjwt.encode(
        {"sub": "u", "roles": ["VIEWER"], "iat": 0, "exp": int(time.time()) - 10},
        # Same secret as the test config sentinel.
        "test-secret-not-for-production-padding",
        algorithm="HS256",
    )
    async with module.create_app().test_client() as client:
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401


async def test_bootstrap_admin_promotes_first_user_when_username_matches(monkeypatch):
    # Re-import the module with the env var set so create_app picks the
    # right BOOTSTRAP_ADMIN_USERNAME at startup time.
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        body = await r.get_json()
        assert body["roles"] == [Role.ADMIN.value]


# ── PATCH /auth/me — Gap #6 self-service profile update ─────────────────


async def test_patch_me_updates_display_name():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        token = (await r.get_json())["access_token"]

        r = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"display_name": "Alice Wonderland"},
        )
        body = await r.get_json()
        assert r.status_code == 200, body
        assert body["display_name"] == "Alice Wonderland"
        assert body["username"] == "alice"

        # Confirm GET /auth/me reflects the change.
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        me = await r.get_json()
        assert me["display_name"] == "Alice Wonderland"


async def test_patch_me_updates_username_and_subsequent_lookup():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        token = (await r.get_json())["access_token"]

        r = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"username": "alice2"},
        )
        body = await r.get_json()
        assert r.status_code == 200, body
        assert body["username"] == "alice2"

        # The JWT still references the same user_id, so /auth/me works
        # and reports the new username.
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        me = await r.get_json()
        assert me["username"] == "alice2"


async def test_patch_me_email_already_in_use_returns_email_taken():
    module = _load_module()
    async with module.create_app().test_client() as client:
        # Register two users; second tries to claim the first's email.
        await _register_and_activate(client, username="alice")
        await _register_and_activate(client, username="bob")
        r = await _login(client, username="bob", password="hunter12345")
        token = (await r.get_json())["access_token"]

        r = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "alice@x.com"},
        )
        body = await r.get_json()
        assert r.status_code == 400
        assert body["code"] == "EMAIL_TAKEN"


async def test_patch_me_username_taken_returns_username_taken():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        await _register_and_activate(client, username="bob")
        r = await _login(client, username="bob", password="hunter12345")
        token = (await r.get_json())["access_token"]

        r = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"username": "alice"},
        )
        body = await r.get_json()
        assert r.status_code == 400
        assert body["code"] == "USERNAME_TAKEN"


async def test_patch_me_without_token_returns_401():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.patch("/api/v1/auth/me", json={"display_name": "Nope"})
        assert r.status_code == 401


async def test_patch_me_with_no_fields_returns_validation_error():
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        token = (await r.get_json())["access_token"]

        r = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        body = await r.get_json()
        assert r.status_code == 400
        assert body["code"] == "VALIDATION_ERROR"


async def test_bootstrap_admin_does_not_promote_non_first_user(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "alice")
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        # Someone else registers first.
        await _register_and_activate(client, username="bob")
        # 'alice' registers second — must NOT be promoted, even though
        # the username matches the env var.
        await _register_and_activate(client, username="alice")
        r = await _login(client, username="alice", password="hunter12345")
        body = await r.get_json()
        assert body["roles"] == [Role.VIEWER.value]
