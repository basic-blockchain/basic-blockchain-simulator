"""KYC admin review endpoints (Phase 6g-admin).

Mounted under `/api/v1/admin/kyc`. Every route is gated by
`@require_permission(Permission.REVIEW_KYC)` so only operators with the
permission (ADMIN by default) can list pending reviews, approve or
reject individual documents and promote a user to the requested level.

The user-side flow that produces these reviews lives in
`api/kyc_routes.py`; this module reuses the same closed set of document
keys, target-level → required-docs mapping and public-document
serialiser to keep the contract identical on both sides.
"""

from __future__ import annotations

from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from api.auth_middleware import require_auth
from api.errors import bad_request
from api.kyc_routes import (
    ALLOWED_DOC_KEYS,
    REQUIRED_DOCS_FOR,
    _public_document,
)
from api.permissions import require_permission
from domain.audit import (
    ACTION_KYC_DOCUMENT_APPROVED,
    ACTION_KYC_DOCUMENT_REJECTED,
    ACTION_KYC_LEVEL_PROMOTED,
)
from domain.permissions import Permission
from domain.user_repository import UserRepositoryProtocol


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_kyc_admin_blueprint(*, users: UserRepositoryProtocol) -> Blueprint:
    """Create the /admin/kyc blueprint bound to the given user repository.

    Routes resolve to:
      GET  /api/v1/admin/kyc/pending
      POST /api/v1/admin/kyc/users/<user_id>/documents/<doc_key>/approve
      POST /api/v1/admin/kyc/users/<user_id>/documents/<doc_key>/reject
      POST /api/v1/admin/kyc/users/<user_id>/promote
    """
    bp = Blueprint("kyc_admin", __name__, url_prefix="/admin/kyc")

    # ── GET /admin/kyc/pending ──────────────────────────────────────────

    @bp.route("/pending", methods=["GET"])
    @require_permission(Permission.REVIEW_KYC)
    async def list_pending():
        pending = [u for u in users.list_users() if u.kyc_pending_review]
        # Oldest submission first so operators work the queue FIFO.
        pending.sort(key=lambda u: u.kyc_submitted_at or "")
        out = []
        for record in pending:
            out.append(
                {
                    "user_id": record.user_id,
                    "username": record.username,
                    "display_name": record.display_name,
                    "kyc_level": record.kyc_level or "L0",
                    "pending_review": record.kyc_pending_review,
                    "submitted_at": record.kyc_submitted_at,
                    "documents": [
                        _public_document(k, record.kyc_documents.get(k))
                        for k in ALLOWED_DOC_KEYS
                    ],
                }
            )
        return jsonify({"users": out, "count": len(out)}), 200

    # ── POST /admin/kyc/users/<user_id>/documents/<doc_key>/approve ────

    @bp.route(
        "/users/<user_id>/documents/<doc_key>/approve", methods=["POST"]
    )
    @require_permission(Permission.REVIEW_KYC)
    async def approve_document(user_id: str, doc_key: str):
        actor = require_auth()
        if doc_key not in ALLOWED_DOC_KEYS:
            return bad_request(
                f"Unknown document key '{doc_key}'.",
                "KYC_UNKNOWN_DOCUMENT_KEY",
            )
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if not target.kyc_pending_review:
            return bad_request(
                "User has no pending KYC review.",
                "KYC_NO_PENDING_REVIEW",
            )
        existing = target.kyc_documents.get(doc_key)
        if not existing:
            return bad_request(
                f"Document '{doc_key}' has not been uploaded.",
                "KYC_DOCUMENT_NOT_UPLOADED",
            )

        new_docs = dict(target.kyc_documents)
        record = dict(existing)
        record["status"] = "verified"
        record["reviewed_at"] = _now_iso()
        # Clear any stale rejection reason from a previous cycle.
        record.pop("reject_reason", None)
        new_docs[doc_key] = record
        users.set_kyc_documents(user_id=user_id, documents=new_docs)
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_KYC_DOCUMENT_APPROVED,
            target_id=user_id,
            details={"key": doc_key, "target_user_id": user_id},
        )
        return jsonify(_public_document(doc_key, record)), 200

    # ── POST /admin/kyc/users/<user_id>/documents/<doc_key>/reject ─────

    @bp.route(
        "/users/<user_id>/documents/<doc_key>/reject", methods=["POST"]
    )
    @require_permission(Permission.REVIEW_KYC)
    async def reject_document(user_id: str, doc_key: str):
        actor = require_auth()
        if doc_key not in ALLOWED_DOC_KEYS:
            return bad_request(
                f"Unknown document key '{doc_key}'.",
                "KYC_UNKNOWN_DOCUMENT_KEY",
            )
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return bad_request(
                "`reason` is required.", "VALIDATION_ERROR",
            )
        reason = reason.strip()

        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if not target.kyc_pending_review:
            return bad_request(
                "User has no pending KYC review.",
                "KYC_NO_PENDING_REVIEW",
            )
        existing = target.kyc_documents.get(doc_key)
        if not existing:
            return bad_request(
                f"Document '{doc_key}' has not been uploaded.",
                "KYC_DOCUMENT_NOT_UPLOADED",
            )

        new_docs = dict(target.kyc_documents)
        record = dict(existing)
        record["status"] = "rejected"
        record["reviewed_at"] = _now_iso()
        record["reject_reason"] = reason
        new_docs[doc_key] = record
        users.set_kyc_documents(user_id=user_id, documents=new_docs)
        # Rejecting any document aborts the whole review cycle — the
        # user must re-upload and resubmit. Clearing pending state here
        # lets them upload again without hitting KYC_REVIEW_IN_PROGRESS.
        users.set_kyc_pending_review(
            user_id=user_id, target=None, submitted_at=None,
        )
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_KYC_DOCUMENT_REJECTED,
            target_id=user_id,
            details={
                "key": doc_key,
                "target_user_id": user_id,
                "reason": reason,
            },
        )
        return jsonify(_public_document(doc_key, record)), 200

    # ── POST /admin/kyc/users/<user_id>/promote ────────────────────────

    @bp.route("/users/<user_id>/promote", methods=["POST"])
    @require_permission(Permission.REVIEW_KYC)
    async def promote_level(user_id: str):
        actor = require_auth()
        target = users.get_user_by_id(user_id)
        if target is None:
            return bad_request("User not found", "USER_NOT_FOUND")
        if not target.kyc_pending_review:
            return bad_request(
                "User has no pending KYC review.",
                "KYC_NO_PENDING_REVIEW",
            )
        target_level = target.kyc_pending_review
        required = REQUIRED_DOCS_FOR.get(target_level, set())
        missing = [
            k for k in sorted(required)
            if (target.kyc_documents.get(k) or {}).get("status") != "verified"
        ]
        if missing:
            return bad_request(
                f"Not all required documents are verified for {target_level}: {missing}.",
                "KYC_NOT_ALL_DOCUMENTS_VERIFIED",
            )

        from_level = target.kyc_level or "L0"
        users.set_kyc_level(user_id=user_id, level=target_level)
        users.set_kyc_pending_review(
            user_id=user_id, target=None, submitted_at=None,
        )
        users.append_audit(
            actor_id=actor.user_id,
            action=ACTION_KYC_LEVEL_PROMOTED,
            target_id=user_id,
            details={
                "from_level": from_level,
                "to_level": target_level,
                "target_user_id": user_id,
            },
        )
        return (
            jsonify(
                {
                    "user_id": user_id,
                    "from_level": from_level,
                    "to_level": target_level,
                }
            ),
            200,
        )

    return bp
