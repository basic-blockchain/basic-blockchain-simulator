-- V019: Phase 6g — Per-user KYC documents and review state.
--
-- Adds the persistence backing for the /me/kyc/* endpoints documented
-- in basic-blockchain-frontend src/api/kyc.ts. `kyc_documents` is a
-- JSONB blob keyed by document type (dni / selfie / address / funds)
-- and stores the lifecycle status of each document together with the
-- base64-encoded payload until a real object storage tier lands.
-- `kyc_pending_review` records the target KYC level the user has
-- submitted for review (NULL when no review is in flight), and
-- `kyc_submitted_at` is the timestamp of that submission for the
-- audit + admin views.
--
-- All new columns are nullable / defaulted so the migration is
-- non-breaking for existing rows.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS kyc_documents       JSONB       DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS kyc_pending_review  VARCHAR(4),
    ADD COLUMN IF NOT EXISTS kyc_submitted_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_kyc_pending_review
    ON users (kyc_pending_review)
    WHERE kyc_pending_review IS NOT NULL;

INSERT INTO schema_migrations (version)
VALUES ('V019')
ON CONFLICT (version) DO NOTHING;
