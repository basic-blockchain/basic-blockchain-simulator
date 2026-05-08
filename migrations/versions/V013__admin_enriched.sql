-- V013: Phase I.5 — Enriched admin API.
--
-- Adds soft-delete support on `users` so an admin can deactivate an
-- account without losing the row (audit, FK targets in audit_log,
-- wallets, transfers all stay intact). The `restore_user` flow flips
-- `deleted_at` back to NULL.
--
-- Also seeds two new permissions (DELETE_USER / RESTORE_USER) into the
-- catalog introduced in V009 so the per-user / per-role override tables
-- can reference them.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_deleted_at ON users (deleted_at);

INSERT INTO permissions (permission_id, description) VALUES
    ('DELETE_USER',  'Soft-delete a user and freeze all their wallets'),
    ('RESTORE_USER', 'Restore a soft-deleted user')
ON CONFLICT (permission_id) DO NOTHING;

INSERT INTO schema_migrations (version)
VALUES ('V013')
ON CONFLICT (version) DO NOTHING;
