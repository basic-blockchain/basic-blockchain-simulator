-- V011: Wallets (Phase I.3).
--
-- Each wallet belongs to one user (1:N user→wallets) and carries a
-- secp256k1 public key derived from a BIP-39 mnemonic generated at
-- creation time. The mnemonic itself is NEVER persisted — it is returned
-- once in the response of `POST /api/v1/wallets` and must be saved by
-- the user (Web3 wallet pattern, MetaMask-style).
--
-- The new transactions/mempool shape required by Phase I.3 carries
-- `sender_wallet_id`, `receiver_wallet_id`, `nonce`, and a hex
-- `signature`, none of which exist in the v0.10.0 schema. This migration
-- TRUNCATES blocks/transactions/mempool to drop the legacy chain — the
-- simulator regenerates a fresh genesis on startup. Acceptable because
-- this is a development simulator (decision authorised by the user
-- 2026-05-07; same precedent as V006 in Phase H+).

TRUNCATE TABLE blocks CASCADE;
TRUNCATE TABLE mempool;

CREATE TABLE IF NOT EXISTS wallets (
    wallet_id    VARCHAR(64)    PRIMARY KEY,
    user_id      VARCHAR(64)    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    currency     VARCHAR(16)    NOT NULL DEFAULT 'NATIVE',
    balance      NUMERIC(28, 8) NOT NULL DEFAULT 0,
    public_key   TEXT           NOT NULL,
    frozen       BOOLEAN        NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallets_user ON wallets (user_id);

-- Re-shape `transactions`: drop the v0.10.0 columns and rebuild around
-- the wallet IDs, nonce, and signature. CASCADE on the TRUNCATE above
-- already cleared the rows.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS sender_wallet_id   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS receiver_wallet_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS nonce              BIGINT,
    ADD COLUMN IF NOT EXISTS signature          TEXT;

-- Re-shape `mempool` so pending transactions can carry the same fields.
ALTER TABLE mempool
    ADD COLUMN IF NOT EXISTS sender_wallet_id   VARCHAR(64),
    ADD COLUMN IF NOT EXISTS receiver_wallet_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS nonce              BIGINT,
    ADD COLUMN IF NOT EXISTS signature          TEXT;

INSERT INTO schema_migrations (version)
VALUES ('V011')
ON CONFLICT (version) DO NOTHING;
