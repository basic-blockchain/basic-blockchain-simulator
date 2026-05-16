"""Phase 6g — KYC user-flow endpoint tests.

Exercises the full happy path (status → upload → submit review) plus
the validation guards (unknown doc keys, missing files, level-skip
rules, in-progress-review locks).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from domain.user_repository import InMemoryUserStore


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _register_and_login(client, *, username: str = "alice"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title(), "email": f"{username}@x.com"},
    )
    body = await r.get_json()
    code = body["activation_code"]
    await client.post(
        "/api/v1/auth/activate",
        json={"username": username, "activation_code": code, "password": "hunter12345"},
    )
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "hunter12345"},
    )
    token = (await r.get_json())["access_token"]
    return body["user_id"], token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── /me/kyc/status ──────────────────────────────────────────────────────


async def test_status_defaults_to_L0_with_all_documents_missing():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        r = await client.get("/api/v1/me/kyc/status", headers=_auth(token))
        assert r.status_code == 200
        body = await r.get_json()
        assert body["level"] == "L0"
        keys = sorted(d["key"] for d in body["documents"])
        assert keys == ["address", "dni", "funds", "selfie"]
        assert all(d["status"] == "missing" for d in body["documents"])
        assert "pending_review" not in body


async def test_status_requires_authentication():
    module = _load_module()
    async with module.create_app().test_client() as client:
        r = await client.get("/api/v1/me/kyc/status")
        assert r.status_code == 401


# ── /me/kyc/documents ───────────────────────────────────────────────────


async def test_upload_document_happy_path_flips_status_to_uploaded():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        r = await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(token),
            json={"key": "dni", "filename": "dni.png", "content_type": "image/png", "data": "QUJD"},
        )
        assert r.status_code == 201
        body = await r.get_json()
        assert body["key"] == "dni"
        assert body["status"] == "uploaded"
        assert body["filename"] == "dni.png"
        # Raw payload must not leak into the API response.
        assert "data" not in body


async def test_upload_document_rejects_unknown_key():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        r = await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(token),
            json={"key": "passport_v2", "filename": "x.png", "content_type": "image/png", "data": "QUJD"},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_UNKNOWN_DOCUMENT_KEY"


async def test_upload_document_rejects_empty_payload():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        r = await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(token),
            json={"key": "dni", "filename": "x.png", "content_type": "image/png", "data": ""},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_INVALID_DOCUMENT_DATA"


# ── /me/kyc/review ──────────────────────────────────────────────────────


async def test_submit_review_flips_uploaded_docs_to_pending_review():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        for key in ("dni", "selfie"):
            await client.post(
                "/api/v1/me/kyc/documents",
                headers=_auth(token),
                json={"key": key, "filename": f"{key}.png", "content_type": "image/png", "data": "QUJD"},
            )
        r = await client.post(
            "/api/v1/me/kyc/review", headers=_auth(token), json={"target": "L1"},
        )
        assert r.status_code == 200
        body = await r.get_json()
        assert body["pending_review"] == "L1"
        doc_status = {d["key"]: d["status"] for d in body["documents"]}
        assert doc_status["dni"] == "pending_review"
        assert doc_status["selfie"] == "pending_review"
        assert doc_status["address"] == "missing"


async def test_submit_review_rejects_missing_documents():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        # Only upload one of the two required L1 docs.
        await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(token),
            json={"key": "dni", "filename": "dni.png", "content_type": "image/png", "data": "QUJD"},
        )
        r = await client.post(
            "/api/v1/me/kyc/review", headers=_auth(token), json={"target": "L1"},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_MISSING_DOCUMENTS"


async def test_submit_review_rejects_level_skip():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        for key in ("dni", "selfie", "address", "funds"):
            await client.post(
                "/api/v1/me/kyc/documents",
                headers=_auth(token),
                json={"key": key, "filename": f"{key}.png", "content_type": "image/png", "data": "QUJD"},
            )
        r = await client.post(
            "/api/v1/me/kyc/review", headers=_auth(token), json={"target": "L3"},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_LEVEL_SKIP_NOT_ALLOWED"


async def test_in_progress_review_blocks_further_uploads_and_submits():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        for key in ("dni", "selfie"):
            await client.post(
                "/api/v1/me/kyc/documents",
                headers=_auth(token),
                json={"key": key, "filename": f"{key}.png", "content_type": "image/png", "data": "QUJD"},
            )
        await client.post(
            "/api/v1/me/kyc/review", headers=_auth(token), json={"target": "L1"},
        )

        r = await client.post(
            "/api/v1/me/kyc/documents",
            headers=_auth(token),
            json={"key": "dni", "filename": "dni.png", "content_type": "image/png", "data": "QUJD"},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_REVIEW_IN_PROGRESS"

        r = await client.post(
            "/api/v1/me/kyc/review", headers=_auth(token), json={"target": "L1"},
        )
        assert r.status_code == 400
        body = await r.get_json()
        assert body["code"] == "KYC_REVIEW_IN_PROGRESS"


# ── /auth/me kyc_level exposure ─────────────────────────────────────────


async def test_auth_me_includes_kyc_level():
    module = _load_module()
    async with module.create_app().test_client() as client:
        _uid, token = await _register_and_login(client)
        r = await client.get("/api/v1/auth/me", headers=_auth(token))
        assert r.status_code == 200
        body = await r.get_json()
        assert body["kyc_level"] == "L0"
        assert "banned" in body
        assert "created_at" in body


# ── In-memory store invariants (no HTTP) ────────────────────────────────


def test_inmemory_store_set_kyc_documents_replaces_full_map():
    s = InMemoryUserStore()
    s.create_user(user_id="u1", username="alice", display_name="A", email=None)
    s.set_kyc_documents(user_id="u1", documents={"dni": {"status": "uploaded"}})
    s.set_kyc_documents(user_id="u1", documents={"selfie": {"status": "uploaded"}})
    rec = s.get_user_by_id("u1")
    assert "dni" not in rec.kyc_documents
    assert rec.kyc_documents["selfie"]["status"] == "uploaded"


def test_inmemory_store_set_kyc_pending_review_round_trip():
    s = InMemoryUserStore()
    s.create_user(user_id="u1", username="alice", display_name="A", email=None)
    s.set_kyc_pending_review(user_id="u1", target="L1", submitted_at="2026-01-01T00:00:00Z")
    rec = s.get_user_by_id("u1")
    assert rec.kyc_pending_review == "L1"
    assert rec.kyc_submitted_at == "2026-01-01T00:00:00Z"
    s.set_kyc_pending_review(user_id="u1", target=None, submitted_at=None)
    rec = s.get_user_by_id("u1")
    assert rec.kyc_pending_review is None


def test_inmemory_store_set_kyc_level_persists():
    s = InMemoryUserStore()
    s.create_user(user_id="u1", username="alice", display_name="A", email=None)
    s.set_kyc_level(user_id="u1", level="L2")
    assert s.get_user_by_id("u1").kyc_level == "L2"


def test_inmemory_store_kyc_keyerror_for_unknown_user():
    s = InMemoryUserStore()
    with pytest.raises(KeyError):
        s.set_kyc_documents(user_id="nope", documents={})
    with pytest.raises(KeyError):
        s.set_kyc_pending_review(user_id="nope", target="L1", submitted_at=None)
    with pytest.raises(KeyError):
        s.set_kyc_level(user_id="nope", level="L1")
