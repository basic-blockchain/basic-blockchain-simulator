-- V018: Phase 5b — Enriched user profile for admin list view.
--
-- Adds country code, KYC level, last-active timestamp and a created-at
-- timestamp to `users` so the admin users table can render the columns
-- defined in docs/ROADMAP.md §4 (KYC / País / Última actividad /
-- Registro) and the filter dropdowns that depend on them.
--
-- All new columns are nullable so this migration is non-breaking for
-- existing rows; the auth flow will start populating them as new users
-- sign up and as activity occurs.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS country     CHAR(2),
    ADD COLUMN IF NOT EXISTS kyc_level   VARCHAR(4) DEFAULT 'L0',
    ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ DEFAULT now();

-- Backfill created_at for legacy rows that predate the column.
UPDATE users SET created_at = now() WHERE created_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_kyc_level   ON users (kyc_level);
CREATE INDEX IF NOT EXISTS idx_users_country     ON users (country);
CREATE INDEX IF NOT EXISTS idx_users_last_active ON users (last_active);

INSERT INTO schema_migrations (version)
VALUES ('V018')
ON CONFLICT (version) DO NOTHING;
