-- V009: Dynamic permissions catalog and per-role / per-user overrides.
--
-- Phase I.2 introduces RBAC. The defaults live in `domain/permissions.py`
-- (`ROLE_PERMISSIONS`); these tables let an ADMIN override the default per
-- role (e.g. give OPERATOR the BAN_USER permission) and grant per-user
-- exceptions without rebuilding the role baseline.
--
-- The 3-level resolution implemented by `has_permission(...)` in the
-- domain module is:
--   user_permissions[user_id] (direct grant)  ─ highest priority
--   role_permissions[role]    (role override)
--   ROLE_PERMISSIONS[role]    (hardcoded default in code)
--
-- See `docs/business-rules.md` BR-RB-01..N for the contract.

CREATE TABLE IF NOT EXISTS permissions (
    permission_id VARCHAR(50)  PRIMARY KEY,
    description   VARCHAR(255) NOT NULL DEFAULT ''
);

-- Seed every permission the Phase I.2 enum defines so `role_permissions`
-- and `user_permissions` rows always reference a known catalog entry.
INSERT INTO permissions (permission_id, description) VALUES
    ('CREATE_USER',         'Register new users on behalf of someone'),
    ('VIEW_USERS',          'List and view user records'),
    ('UPDATE_USER',         'Edit user profile fields'),
    ('BAN_USER',            'Ban a user (prevents login + revokes access)'),
    ('UNBAN_USER',          'Lift a ban'),
    ('ASSIGN_ROLE',         'Grant or revoke roles on a user'),
    ('MANAGE_PERMISSIONS',  'Override role / user permission rows'),
    ('VIEW_AUDIT_LOG',      'Read the admin audit trail'),
    ('CREATE_WALLET',       'Create a wallet (Phase I.3)'),
    ('TRANSFER',            'Sign and submit a transfer (Phase I.3)'),
    ('MINT',                'Mint native coin into a wallet (Phase I.3)'),
    ('FREEZE_WALLET',       'Freeze a wallet so it cannot transfer (Phase I.3)'),
    ('UNFREEZE_WALLET',     'Lift a wallet freeze (Phase I.3)'),
    ('VIEW_WALLETS',        'List wallets across users'),
    ('VIEW_TRANSFERS',      'View confirmed transfers across the chain')
ON CONFLICT (permission_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS role_permissions (
    role          user_role    NOT NULL,
    permission_id VARCHAR(50)  NOT NULL REFERENCES permissions(permission_id),
    granted_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (role, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_role ON role_permissions (role);

CREATE TABLE IF NOT EXISTS user_permissions (
    user_id       VARCHAR(64)  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    permission_id VARCHAR(50)  NOT NULL REFERENCES permissions(permission_id),
    granted_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_user_permissions_user ON user_permissions (user_id);
CREATE INDEX IF NOT EXISTS idx_user_permissions_perm ON user_permissions (permission_id);

-- Ban flag on `users` so a banned account is rejected at login without
-- relying on a separate "is active" column. Keeps the ban + unban
-- endpoints idempotent.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS banned BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO schema_migrations (version)
VALUES ('V009')
ON CONFLICT (version) DO NOTHING;
