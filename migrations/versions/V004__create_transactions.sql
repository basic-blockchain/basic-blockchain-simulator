-- V004: Confirmed transactions attached to mined blocks.
--
-- References blocks(index) — the natural domain key — rather than blocks(id)
-- so that application code can join on the value it already knows without an
-- extra lookup.  ON DELETE CASCADE propagates if a block is ever rolled back
-- (chain reorganisation scenario).

CREATE TABLE IF NOT EXISTS transactions (
    id          SERIAL          PRIMARY KEY,
    block_index INTEGER         NOT NULL
                                REFERENCES blocks (index)
                                ON DELETE CASCADE,
    sender      TEXT            NOT NULL,
    receiver    TEXT            NOT NULL,
    amount      NUMERIC(20, 8)  NOT NULL CHECK (amount > 0)
);

-- Primary access pattern: "give me all transactions for block N".
CREATE INDEX IF NOT EXISTS idx_transactions_block_index
    ON transactions (block_index);

-- Secondary access pattern: activity history per participant.
CREATE INDEX IF NOT EXISTS idx_transactions_sender   ON transactions (sender);
CREATE INDEX IF NOT EXISTS idx_transactions_receiver ON transactions (receiver);

INSERT INTO schema_migrations (version)
VALUES ('V004')
ON CONFLICT (version) DO NOTHING;
