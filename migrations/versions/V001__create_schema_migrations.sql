-- V001: Bootstrap the migration tracking table.
-- Idempotent: CREATE TABLE IF NOT EXISTS guarantees safe re-runs.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

INSERT INTO schema_migrations (version)
VALUES ('V001')
ON CONFLICT (version) DO NOTHING;
