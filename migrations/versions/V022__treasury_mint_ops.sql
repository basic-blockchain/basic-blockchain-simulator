-- V022: Phase 7.8.1 — Threshold-gated mint dual-sign operations.
--
-- Backs the above-threshold path of POST /admin/mint and its
-- approve/cancel/list siblings, as specified in
-- docs/specs/7.8.0-treasury-dual-sign.md §3.2. When the mint amount
-- is below `MINT_DUAL_SIGN_THRESHOLD` (config; default 0 = disabled)
-- the existing synchronous /admin/mint behavior is preserved bit-for-
-- bit and no row is written here (BR-TR-07). When the amount meets or
-- exceeds the threshold, a row lands in `pending_approval` and a
-- second admin must approve before the coinbase tx is built.
--
-- Schema notes:
--   * `op_id` is server-generated with the `tmo_` prefix (treasury
--     mint op). It never collides with distribution op ids (`tdo_`,
--     V021).
--   * `executed_tx_id` is a single id (vs the JSONB array in V021):
--     a dual-sign mint emits exactly one coinbase tx, identical in
--     shape to today's synchronous /admin/mint result (BR-TR-08).
--   * `chk_mint_same_signer` mirrors V021's BR-TR-01 enforcement at
--     the DB level as defence-in-depth. NULL approver (pending op) is
--     allowed; equality to `initiated_by` is rejected.
--   * `chk_mint_status` pins the state machine values (BR-TR-05).
--
-- The mirror in-memory `TreasuryMintOpRecord` and the dual-sign
-- branch of `MintService` land in 7.8.2 / 7.8.3 / 7.8.5. Applying
-- this migration ahead of the code is safe — the existing
-- `/admin/mint` route does not look at this table.

CREATE TABLE IF NOT EXISTS treasury_mint_ops (
    op_id            VARCHAR(64) PRIMARY KEY,
    status           VARCHAR(32)    NOT NULL,
    currency         VARCHAR(16)    NOT NULL,
    target_wallet_id VARCHAR(64)    NOT NULL,
    amount           NUMERIC(38, 18) NOT NULL,
    reason           TEXT,
    initiated_by     VARCHAR(64)    NOT NULL,
    initiated_at     TIMESTAMPTZ    NOT NULL,
    approved_by      VARCHAR(64),
    approved_at      TIMESTAMPTZ,
    executed_at      TIMESTAMPTZ,
    cancelled_at     TIMESTAMPTZ,
    executed_tx_id   VARCHAR(64),
    CONSTRAINT chk_mint_status
        CHECK (status IN ('pending_approval', 'executed', 'cancelled')),
    CONSTRAINT chk_mint_same_signer
        CHECK (approved_by IS NULL OR approved_by <> initiated_by)
);

CREATE INDEX IF NOT EXISTS idx_treasury_mint_ops_status
    ON treasury_mint_ops (status, initiated_at DESC);

CREATE INDEX IF NOT EXISTS idx_treasury_mint_ops_initiated_by
    ON treasury_mint_ops (initiated_by);

INSERT INTO schema_migrations (version)
VALUES ('V022')
ON CONFLICT (version) DO NOTHING;
