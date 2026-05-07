-- V008: User credentials, roles, and activation codes (Phase I.1).
--
-- `user_credentials` stores the bcrypt password hash for each user. The
-- `activation_code` column carries a one-shot code created at registration:
-- a fresh user starts with `password_hash = ''` and an `activation_code`,
-- and must call `POST /api/v1/auth/activate` with that code plus a chosen
-- password to enable login. The code is cleared once consumed.
--
-- `user_roles` holds the role assignments. A user can have several roles
-- (e.g. ADMIN + OPERATOR for staff accounts). The default-on-registration
-- behaviour is documented in `domain/auth.py`. Phase I.2 will introduce
-- per-role and per-user permission overrides on top of these defaults.

CREATE TYPE user_role AS ENUM ('ADMIN', 'OPERATOR', 'VIEWER');

CREATE TABLE IF NOT EXISTS user_credentials (
    user_id              VARCHAR(64)  PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash        VARCHAR(128) NOT NULL DEFAULT '',
    activation_code      VARCHAR(32),
    activated_at         TIMESTAMPTZ,
    must_change_password BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id    VARCHAR(64) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role       user_role   NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles (user_id);

INSERT INTO schema_migrations (version)
VALUES ('V008')
ON CONFLICT (version) DO NOTHING;
