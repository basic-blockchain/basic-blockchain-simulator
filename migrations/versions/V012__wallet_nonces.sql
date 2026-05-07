-- V012: Per-wallet nonce table for replay protection (Phase I.3).
--
-- Every signed transfer carries a nonce. The server only accepts a
-- transfer when its nonce is strictly greater than the last accepted
-- nonce for the sender wallet. The row is UPSERTed inside the same DB
-- transaction that admits the tx into the mempool, so two concurrent
-- requests with the same nonce cannot both succeed.

CREATE TABLE IF NOT EXISTS wallet_nonces (
    wallet_id        VARCHAR(64) PRIMARY KEY REFERENCES wallets(wallet_id) ON DELETE CASCADE,
    last_used_nonce  BIGINT      NOT NULL DEFAULT 0,
    last_used_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version)
VALUES ('V012')
ON CONFLICT (version) DO NOTHING;
