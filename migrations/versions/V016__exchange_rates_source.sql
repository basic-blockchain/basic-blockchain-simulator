-- V016: Exchange rate sources (external feeds).
--
-- Adds a source tag so rates can be attributed to MANUAL or a provider.

ALTER TABLE exchange_rates
    ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'MANUAL';

CREATE INDEX IF NOT EXISTS idx_exchange_rates_source
    ON exchange_rates (source);

INSERT INTO schema_migrations (version)
VALUES ('V016')
ON CONFLICT (version) DO NOTHING;
