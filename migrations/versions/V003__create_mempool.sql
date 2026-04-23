-- V003: Mempool — pending (unconfirmed) transactions waiting to be mined.
--
-- Rows are inserted when a transaction is submitted and deleted once the
-- block that includes them is mined and written to `transactions`.
-- NUMERIC(20,8) matches Python Decimal precision used throughout this project.

CREATE TABLE IF NOT EXISTS mempool (
    id         SERIAL           PRIMARY KEY,
    sender     TEXT             NOT NULL,
    receiver   TEXT             NOT NULL,
    amount     NUMERIC(20, 8)   NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ      DEFAULT NOW() NOT NULL
);

-- Ordered drain: when mining, consume the oldest pending transactions first.
CREATE INDEX IF NOT EXISTS idx_mempool_created_at ON mempool (created_at);

INSERT INTO schema_migrations (version)
VALUES ('V003')
ON CONFLICT (version) DO NOTHING;
