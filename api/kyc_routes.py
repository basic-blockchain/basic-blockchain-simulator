"""KYC user-flow endpoints (Phase 6g).

Implements the contract documented in
`basic-blockchain-frontend/src/api/kyc.ts`:

  GET  /me/kyc/status     → current level + per-doc status + review state
  POST /me/kyc/documents  → upload a single document (base64 in body)
  POST /me/kyc/review     → submit the requested target level for review

All routes are gated by `require_auth()` — any authenticated user can
manage their own KYC state. Admin-side review/approval lives elsewhere
(Phase 6e backend, not in this PR).
"""

from __future__ import annotations

from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from domain.audit import (
    ACTION_KYC_DOCUMENT_UPLOADED,
    ACTION_KYC_REVIEW_REQUESTED,
)
from domain.user_repository import UserRepositoryProtocol


# Document keys the frontend ships in src/api/kyc.ts. Anything outside
# this set is rejected up-front so we never persist unknown shapes.
ALLOWED_DOC_KEYS = frozenset({"dni", "selfie", "address", "funds"})

# Allowed level transitions for the review submission. L0 is the starting
# point; users can request L1 → L2 → L3 in order but not skip levels.
LEVEL_ORDER = ["L0", "L1", "L2", "L3"]
ALLOWED_REVIEW_TARGETS = frozenset({"L1", "L2", "L3"})

# Per-target minimum document set. Aligns with the minLevel field on the
# frontend's KYC_DOCS array so the gate is identical client- and
# server-side.
REQUIRED_DOCS_FOR: dict[str, set[str]] = {
    "L1": {"dni", "selfie"},
    "L2": {"dni", "selfie", "address"},
    "L3": {"dni", "selfie", "address", "funds"},
}

# Fields stored inside `kyc_documents[key]` that are safe to return in
# the API response. Anything not in this set (notably the raw base64
# `data` payload) is stripped before serialisation.
_PUBLIC_DOC_FIELDS = (
    "key", "status", "uploaded_at", "reviewed_at", "reject_reason",
    "content_type", "filename",
)


def _public_document(key: str, raw: dict[str, object] | None) -> dict[str, object]:
    """Return the API-safe view of one document record."""
    out: dict[str, object] = {"key": key, "status": "missing"}
    if not raw:
        return out
    for field in _PUBLIC_DOC_FIELDS:
        if field in raw and raw[field] is not None:
            out[field] = raw[field]
    out["key"] = key  # always echo the key, even if absent from storage
    return out


def _status_payload(user) -> dict[str, object]:
    documents = [_public_document(k, user.kyc_documents.get(k)) for k in ALLOWED_DOC_KEYS]
    payload: dict[str, object] = {
        "level": user.kyc_level or "L0",
        "documents": documents,
    }
    if user.kyc_pending_review:
        payload["pending_review"] = user.kyc_pending_review
    if user.kyc_submitted_at:
        payload["submitted_at"] = user.kyc_submitted_at
    return payload


def build_kyc_blueprint(*, users: UserRepositoryProtocol) -> Blueprint:
    """Create the /me/kyc blueprint bound to the given user repository.

    Mounted at `/api/v1/me/kyc` so routes resolve to
    `/api/v1/me/kyc/status`, `/api/v1/me/kyc/documents`, and
    `/api/v1/me/kyc/review`.
    """
    bp = Blueprint("kyc", __name__, url_prefix="/me/kyc")

    # ── GET /me/kyc/status ──────────────────────────────────────────

    @bp.route("/status", methods=["GET"])
    async def status():
        current = require_auth()
        user = users.get_user_by_id(current.user_id)
        if user is None:
            return bad_request("User not found", "AUTH_USER_NOT_FOUND")
        return jsonify(_status_payload(user)), 200

    # ── POST /me/kyc/documents ──────────────────────────────────────

    @bp.route("/documents", methods=["POST"])
    async def upload_document():
        current = require_auth()
        user = users.get_user_by_id(current.user_id)
        if user is None:
            return bad_request("User not found", "AUTH_USER_NOT_FOUND")
        if user.kyc_pending_review:
            return bad_request(
                "Cannot modify documents while a review is in progress.",
                "KYC_REVIEW_IN_PROGRESS",
            )
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        key = (data.get("key") or "").strip()
        if key not in ALLOWED_DOC_KEYS:
            return bad_request(
                f"Unknown document key '{key}'.", "KYC_UNKNOWN_DOCUMENT_KEY",
            )
        payload = data.get("data")
        if not isinstance(payload, str) or not payload:
            return bad_request(
                "Document `data` must be a non-empty base64 string.",
                "KYC_INVALID_DOCUMENT_DATA",
            )
        filename = data.get("filename")
        content_type = data.get("content_type")
        if not isinstance(filename, str) or not filename:
            return bad_request("`filename` is required.", "VALIDATION_ERROR")
        if not isinstance(content_type, str) or not content_type:
            return bad_request("`content_type` is required.", "VALIDATION_ERROR")

        uploaded_at = datetime.now(timezone.utc).isoformat()
        record: dict[str, object] = {
            "key": key,
            "status": "uploaded",
            "uploaded_at": uploaded_at,
            "filename": filename,
            "content_type": content_type,
            # Raw payload kept in storage but never returned in API responses
            # (see `_public_document`). When object storage lands this moves
            # into a separate column or a side-table.
            "data": payload,
        }

        new_docs = dict(user.kyc_documents)
        new_docs[key] = record
        users.set_kyc_documents(user_id=user.user_id, documents=new_docs)
        users.append_audit(
            actor_id=user.user_id,
            action=ACTION_KYC_DOCUMENT_UPLOADED,
            target_id=user.user_id,
            details={"key": key, "content_type": content_type, "filename": filename},
        )

        return jsonify(_public_document(key, record)), 201

    # ── POST /me/kyc/review ─────────────────────────────────────────

    @bp.route("/review", methods=["POST"])
    async def submit_review():
        current = require_auth()
        user = users.get_user_by_id(current.user_id)
        if user is None:
            return bad_request("User not found", "AUTH_USER_NOT_FOUND")
        if user.kyc_pending_review:
            return bad_request(
                "A review is already in progress.", "KYC_REVIEW_IN_PROGRESS",
            )
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        target = (data.get("target") or "").strip()
        if target not in ALLOWED_REVIEW_TARGETS:
            return bad_request(
                f"Invalid review target '{target}'. Allowed: L1, L2, L3.",
                "KYC_INVALID_REVIEW_TARGET",
            )

        # Reject skipping levels. Submitting L3 while sitting at L0 should
        # go through L1 → L2 → L3.
        current_idx = LEVEL_ORDER.index(user.kyc_level or "L0")
        target_idx = LEVEL_ORDER.index(target)
        if target_idx != current_idx + 1:
            return bad_request(
                f"Cannot jump from {user.kyc_level or 'L0'} to {target}; "
                "review the next level only.",
                "KYC_LEVEL_SKIP_NOT_ALLOWED",
            )

        required = REQUIRED_DOCS_FOR.get(target, set())
        missing = [
            k for k in required
            if (user.kyc_documents.get(k) or {}).get("status")
            not in {"uploaded", "pending_review", "verified"}
        ]
        if missing:
            return bad_request(
                f"Missing documents for {target}: {sorted(missing)}.",
                "KYC_MISSING_DOCUMENTS",
            )

        submitted_at = datetime.now(timezone.utc).isoformat()
        new_docs = dict(user.kyc_documents)
        for k in required:
            doc = dict(new_docs.get(k) or {})
            if doc.get("status") == "uploaded":
                doc["status"] = "pending_review"
                new_docs[k] = doc
        users.set_kyc_documents(user_id=user.user_id, documents=new_docs)
        users.set_kyc_pending_review(
            user_id=user.user_id, target=target, submitted_at=submitted_at,
        )
        users.append_audit(
            actor_id=user.user_id,
            action=ACTION_KYC_REVIEW_REQUESTED,
            target_id=user.user_id,
            details={"target": target},
        )

        refreshed = users.get_user_by_id(user.user_id)
        assert refreshed is not None  # row exists; we just wrote to it
        return jsonify(_status_payload(refreshed)), 200

    return bp
