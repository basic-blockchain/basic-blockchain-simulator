-- V014: Currency catalog + wallet types (MC-1/MC-2).
--
-- Adds a currency catalog, a wallet_type enum (USER/TREASURY/FEE),
-- and enforces wallet currency via FK. Also seeds the NATIVE currency
-- and a SYSTEM user for treasury ownership.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'wallet_type') THEN
        CREATE TYPE wallet_type AS ENUM ('USER', 'TREASURY', 'FEE');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS currencies (
    code       VARCHAR(10)  PRIMARY KEY,
    name       TEXT         NOT NULL,
    decimals   SMALLINT     NOT NULL DEFAULT 8,
    active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

INSERT INTO currencies (code, name, decimals, active)
VALUES ('NATIVE', 'Native', 8, TRUE)
ON CONFLICT (code) DO NOTHING;

ALTER TABLE wallets
    ADD COLUMN IF NOT EXISTS wallet_type wallet_type NOT NULL DEFAULT 'USER';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wallets_currency_fk'
    ) THEN
        ALTER TABLE wallets
            ADD CONSTRAINT wallets_currency_fk
            FOREIGN KEY (currency) REFERENCES currencies(code);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallets_treasury_currency
    ON wallets (currency)
    WHERE wallet_type = 'TREASURY';

INSERT INTO users (user_id, username, display_name, email)
VALUES ('SYSTEM', 'system', 'System Treasury', NULL)
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO permissions (permission_id, description) VALUES
    ('CREATE_CURRENCY', 'Create or update a currency catalog entry'),
    ('CREATE_TREASURY_WALLET', 'Create a treasury wallet for a currency'),
    ('MANAGE_EXCHANGE_RATES', 'Set exchange rates and fees')
ON CONFLICT (permission_id) DO NOTHING;

INSERT INTO schema_migrations (version)
VALUES ('V014')
ON CONFLICT (version) DO NOTHING;
