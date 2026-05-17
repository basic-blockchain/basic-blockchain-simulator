-- V021: Phase 7.8.1 — Treasury distribution operations (dual-sign envelope).
--
-- Backs POST /admin/treasury/distribute and its approve/cancel/list
-- siblings, as specified in docs/specs/7.8.0-treasury-dual-sign.md
-- §3.1. Each row is one distribution op: a treasury wallet pays
-- `amount_per_wallet` to N recipient users (one transfer per
-- recipient) only after a second admin approves.
--
-- Schema notes:
--   * `op_id` is server-generated with the `tdo_` prefix (treasury
--     distribution op) so it never collides with mint-op ids (`tmo_`,
--     migration V022) or with any other id surface.
--   * `recipient_user_ids` is JSONB (array<string>) so the column shape
--     mirrors the request body and the in-memory dataclass without an
--     extra junction table. Duplicates are rejected at the service
--     layer (BR-TR-03), not by a UNIQUE inside the JSON.
--   * `executed_tx_ids` is NULL until the op reaches `executed`; on
--     execute the service writes the N transfer ids atomically in the
--     same statement that flips `status` to `executed` (BR-TR-04).
--   * `chk_dist_same_signer` enforces BR-TR-01 at the DB level as
--     defence-in-depth; the service layer enforces it first. The
--     constraint allows NULL approver (pending op) and rejects only
--     the equality `approved_by = initiated_by`.
--   * `chk_dist_status` pins the state machine values (BR-TR-05).
--
-- The mirror in-memory `TreasuryDistributionRecord` lands in 7.8.2;
-- the routes that read/write this table land in 7.8.4. Until then
-- the table exists but is unused — applying the migration on a
-- production node ahead of the code is safe.

CREATE TABLE IF NOT EXISTS treasury_distributions (
    op_id              VARCHAR(64) PRIMARY KEY,
    status             VARCHAR(32)    NOT NULL,
    currency           VARCHAR(16)    NOT NULL,
    source_wallet_id   VARCHAR(64)    NOT NULL,
    amount_per_wallet  NUMERIC(38, 18) NOT NULL,
    recipient_user_ids JSONB          NOT NULL,
    memo               TEXT,
    initiated_by       VARCHAR(64)    NOT NULL,
    initiated_at       TIMESTAMPTZ    NOT NULL,
    approved_by        VARCHAR(64),
    approved_at        TIMESTAMPTZ,
    executed_at        TIMESTAMPTZ,
    cancelled_at       TIMESTAMPTZ,
    executed_tx_ids    JSONB,
    CONSTRAINT chk_dist_status
        CHECK (status IN ('pending_approval', 'executed', 'cancelled')),
    CONSTRAINT chk_dist_same_signer
        CHECK (approved_by IS NULL OR approved_by <> initiated_by)
);

CREATE INDEX IF NOT EXISTS idx_treasury_distributions_status
    ON treasury_distributions (status, initiated_at DESC);

CREATE INDEX IF NOT EXISTS idx_treasury_distributions_initiated_by
    ON treasury_distributions (initiated_by);

INSERT INTO schema_migrations (version)
VALUES ('V021')
ON CONFLICT (version) DO NOTHING;
