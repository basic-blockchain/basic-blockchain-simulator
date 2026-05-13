-- V015: Exchange rates (MC-3).
--
-- Stores an append-only history of exchange rates + fees.

CREATE TABLE IF NOT EXISTS exchange_rates (
    rate_id       BIGSERIAL   PRIMARY KEY,
    from_currency VARCHAR(10) NOT NULL REFERENCES currencies(code),
    to_currency   VARCHAR(10) NOT NULL REFERENCES currencies(code),
    rate          NUMERIC(28, 8) NOT NULL CHECK (rate > 0),
    fee_rate      NUMERIC(8, 6)  NOT NULL DEFAULT 0 CHECK (fee_rate >= 0 AND fee_rate <= 1),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exchange_rates_pair_time
    ON exchange_rates (from_currency, to_currency, updated_at DESC);

INSERT INTO schema_migrations (version)
VALUES ('V015')
ON CONFLICT (version) DO NOTHING;
