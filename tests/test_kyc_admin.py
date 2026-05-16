"""Phase 6g-admin — KYC admin review endpoint tests.

Covers:
- GET  /admin/kyc/pending lists every user with kyc_pending_review,
  sorted by submission timestamp.
- POST .../documents/<key>/approve flips a single doc to 'verified'
  and stamps reviewed_at.
- POST .../documents/<key>/reject flips to 'rejected' (with reason),
  clears the pending review so the user can re-upload, and refuses
  empty reasons.
- POST .../promote moves the user to the pending target iff every
  required document is verified; rejects with KYC_NOT_ALL_DOCUMENTS_VERIFIED
  otherwise.
- All routes are gated by Permission.REVIEW_KYC (403 for non-admins).
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


async def _login_and_token(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    )
    assert r.status_code == 200, await r.get_json()
    body = await r.get_json()
    return body["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token: str, *, key: str) -> None:
    r = await client.post(
        "/api/v1/me/kyc/documents",
        headers=_auth(token),
        json={"key": key, "filename": f"{key}.png", "content_type": "image/png", "data": "QUJD"},
    )
    assert r.status_code == 201, await r.get_json()


async def _submit_review(client, token: str, *, target: str) -> None:
    r = await client.post(
        "/api/v1/me/kyc/review", headers=_auth(token), json={"target": target},
    )
    assert r.status_code == 200, await r.get_json()


async def _bootstrap(monkeypatch, *, admin_username: str = "alice"):
    """Create an ADMIN + a regular user with a pending L1 review."""
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", admin_username)
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    client_cm = module.create_app().test_client()
    return module, client_cm


async def test_pending_lists_users_with_in_progress_review(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")  # admin
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")

        r = await client.get("/api/v1/admin/kyc/pending", headers=_auth(admin_token))
        assert r.status_code == 200
        body = await r.get_json()
        assert body["count"] == 1
        only = body["users"][0]
        assert only["user_id"] == bob_id
        assert only["pending_review"] == "L1"
        assert only["kyc_level"] == "L0"
        assert only["submitted_at"]
        statuses = {d["key"]: d["status"] for d in only["documents"]}
        assert statuses["dni"] == "pending_review"
        assert statuses["selfie"] == "pending_review"
        assert statuses["address"] == "missing"
        # Raw base64 never leaks.
        for d in only["documents"]:
            assert "data" not in d


async def test_pending_requires_review_kyc_permission(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "")  # nobody auto-promotes
    import importlib
    import config

    importlib.reload(config)
    module = _load_module()
    async with module.create_app().test_client() as client:
        await _register_activate(client, username="bob")  # default VIEWER
        token = await _login_and_token(client, username="bob")
        r = await client.get("/api/v1/admin/kyc/pending", headers=_auth(token))
        assert r.status_code == 403


async def test_approve_flips_status_to_verified(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")

        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200, await r.get_json()
        body = await r.get_json()
        assert body["key"] == "dni"
        assert body["status"] == "verified"
        assert body["reviewed_at"]


async def test_approve_rejects_unknown_user(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        admin_token = await _login_and_token(client, username="alice")
        r = await client.post(
            "/api/v1/admin/kyc/users/does-not-exist/documents/dni/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "USER_NOT_FOUND"


async def test_approve_rejects_user_without_pending_review(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_NO_PENDING_REVIEW"


async def test_approve_rejects_unknown_document_key(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/passport_v2/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_UNKNOWN_DOCUMENT_KEY"


async def test_approve_rejects_document_not_uploaded(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/address/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_DOCUMENT_NOT_UPLOADED"


async def test_reject_records_reason_and_clears_pending(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")

        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/reject",
            headers=_auth(admin_token),
            json={"reason": "blurry photo"},
        )
        assert r.status_code == 200, await r.get_json()
        body = await r.get_json()
        assert body["status"] == "rejected"
        assert body["reject_reason"] == "blurry photo"

        # The whole review is aborted — bob can upload again.
        status = await client.get(
            "/api/v1/me/kyc/status", headers=_auth(bob_token),
        )
        sbody = await status.get_json()
        assert "pending_review" not in sbody
        r = await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(bob_token),
            json={"key": "dni", "filename": "dni.png", "content_type": "image/png", "data": "QUJD"},
        )
        assert r.status_code == 201


async def test_reject_requires_reason(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/reject",
            headers=_auth(admin_token),
            json={"reason": "   "},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "VALIDATION_ERROR"


async def test_promote_requires_all_docs_verified(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")

        # Only one of the two L1 docs verified — promote must fail.
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/approve",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/promote",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_NOT_ALL_DOCUMENTS_VERIFIED"


async def test_promote_happy_path_moves_user_to_target(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")
        for key in ("dni", "selfie"):
            r = await client.post(
                f"/api/v1/admin/kyc/users/{bob_id}/documents/{key}/approve",
                headers=_auth(admin_token),
            )
            assert r.status_code == 200

        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/promote",
            headers=_auth(admin_token),
        )
        assert r.status_code == 200, await r.get_json()
        body = await r.get_json()
        assert body["from_level"] == "L0"
        assert body["to_level"] == "L1"

        # User-side status reflects the promotion and clears pending.
        status = await client.get(
            "/api/v1/me/kyc/status", headers=_auth(bob_token),
        )
        sbody = await status.get_json()
        assert sbody["level"] == "L1"
        assert "pending_review" not in sbody


async def test_promote_rejects_user_without_pending(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        r = await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/promote",
            headers=_auth(admin_token),
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_NO_PENDING_REVIEW"


async def test_admin_actions_appear_in_audit_log(monkeypatch):
    _module, client_cm = await _bootstrap(monkeypatch)
    async with client_cm as client:
        await _register_activate(client, username="alice")
        bob_id = await _register_activate(client, username="bob")
        admin_token = await _login_and_token(client, username="alice")
        bob_token = await _login_and_token(client, username="bob")
        await _upload(client, bob_token, key="dni")
        await _upload(client, bob_token, key="selfie")
        await _submit_review(client, bob_token, target="L1")
        await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/dni/approve",
            headers=_auth(admin_token),
        )
        await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/documents/selfie/approve",
            headers=_auth(admin_token),
        )
        await client.post(
            f"/api/v1/admin/kyc/users/{bob_id}/promote",
            headers=_auth(admin_token),
        )

        r = await client.get(
            "/api/v1/admin/audit?limit=50", headers=_auth(admin_token),
        )
        assert r.status_code == 200
        actions = {e["action"] for e in (await r.get_json())["entries"]}
        assert "KYC_DOCUMENT_APPROVED" in actions
        assert "KYC_LEVEL_PROMOTED" in actions
