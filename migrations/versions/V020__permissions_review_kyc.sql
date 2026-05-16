-- V020: Seed the REVIEW_KYC permission (Phase 6g-admin).
--
-- The Phase 6g-admin endpoints (/admin/kyc/*) gate access on a new
-- Permission.REVIEW_KYC entry. Persistence rows in `role_permissions`
-- and `user_permissions` reference `permissions(permission_id)` via FK,
-- so the catalog must carry this id before any override can be granted.

INSERT INTO permissions (permission_id, description) VALUES
    ('REVIEW_KYC', 'List pending KYC reviews, approve/reject documents and promote levels')
ON CONFLICT (permission_id) DO NOTHING;

INSERT INTO schema_migrations (version)
VALUES ('V020')
ON CONFLICT (version) DO NOTHING;
