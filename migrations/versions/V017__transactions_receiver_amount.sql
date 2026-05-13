-- V017: Receiver amount for cross-currency transfers.
--
-- When sender and receiver wallets use different currencies, the
-- receiver is credited with a converted amount. Store the credited
-- amount alongside the sender amount so mining can apply accurate
-- deltas without re-fetching rates.

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS receiver_amount NUMERIC(28, 8);

ALTER TABLE mempool
    ADD COLUMN IF NOT EXISTS receiver_amount NUMERIC(28, 8);

INSERT INTO schema_migrations (version)
VALUES ('V017')
ON CONFLICT (version) DO NOTHING;
