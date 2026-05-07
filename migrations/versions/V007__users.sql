-- V007: Users table (Phase I.1).
--
-- Phase I introduces real identity. `users` stores the canonical user record;
-- `user_id` is a server-generated opaque string (hex) used as the JWT `sub`
-- claim and as the FK target for credentials, roles, wallets, and audit log.
-- `username` is the human-friendly handle used at login.
--
-- Multi-tenancy is intentionally NOT modelled here (decision #1 in the Phase
-- I plan): the simulator is single-org, so there is no `tenant_id`.

CREATE TABLE IF NOT EXISTS users (
    user_id      VARCHAR(64)  PRIMARY KEY,
    username     VARCHAR(64)  NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    email        VARCHAR(255) UNIQUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);

INSERT INTO schema_migrations (version)
VALUES ('V007')
ON CONFLICT (version) DO NOTHING;
