-- V002: Persistent storage for mined blocks.
--
-- `index` carries domain meaning (chain position, 1-based) and is declared
-- UNIQUE so it can serve as a FK target from the transactions table without
-- requiring a second unique index.  `id` is the internal surrogate PK kept
-- consistent with the rest of the schema.

CREATE TABLE IF NOT EXISTS blocks (
    id            SERIAL       PRIMARY KEY,
    index         INTEGER      NOT NULL UNIQUE,
    timestamp     TEXT         NOT NULL,
    proof         INTEGER      NOT NULL,
    previous_hash TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  DEFAULT NOW() NOT NULL
);

-- Fast lookup by chain position (covers the UNIQUE constraint index already,
-- but named explicitly for query-plan readability).
CREATE INDEX IF NOT EXISTS idx_blocks_index ON blocks (index);

INSERT INTO schema_migrations (version)
VALUES ('V002')
ON CONFLICT (version) DO NOTHING;
