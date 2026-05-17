-- V023: Seed the treasury dual-sign permissions (Phase 7.8.6).
--
-- The Phase 7.8 endpoints (`/admin/treasury/distribute/*` and the
-- approve/cancel/list siblings on `/admin/mint/*`) gate access on
-- five new Permission entries. Persistence rows in `role_permissions`
-- and `user_permissions` reference `permissions(permission_id)` via
-- FK, so the catalog must carry these ids before any override or
-- baseline grant can be persisted.
--
-- The baseline role grants themselves (ADMIN holding initiate /
-- approve / view for distributions and approve / view for mint ops;
-- OPERATOR holding only the two VIEW_* perms) live in
-- `domain/permissions.py::ROLE_PERMISSIONS` and are evaluated at
-- request time. This migration only ensures the FK target exists
-- so a `POST /admin/users/<id>/permissions` can store an override
-- for any of these ids without a foreign-key violation.

INSERT INTO permissions (permission_id, description) VALUES
    ('INITIATE_TREASURY_DISTRIBUTION', 'Create a pending treasury distribution op and cancel one initiated by self'),
    ('APPROVE_TREASURY_DISTRIBUTION',  'Approve and execute a pending treasury distribution op initiated by someone else'),
    ('VIEW_TREASURY_DISTRIBUTIONS',    'List and read treasury distribution ops (read-only)'),
    ('APPROVE_TREASURY_MINT_OP',       'Approve and execute a pending dual-sign mint op initiated by someone else'),
    ('VIEW_TREASURY_MINT_OPS',         'List and read dual-sign mint ops (read-only)')
ON CONFLICT (permission_id) DO NOTHING;

INSERT INTO schema_migrations (version)
VALUES ('V023')
ON CONFLICT (version) DO NOTHING;
