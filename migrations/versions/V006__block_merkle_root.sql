-- V006: Block carries its Merkle root.
--
-- Phase H+ adds `Block.merkle_root` (and `Block.transactions`, hydrated at
-- read time from the existing `transactions` table). The chain hash now
-- covers the Merkle root so any post-hoc edit to the `transactions` table
-- becomes detectable by `is_chain_valid()`.
--
-- The existing chain on disk was hashed with the pre-Phase-H+ payload
-- (no merkle_root). Continuing it under the new hash format would break
-- chain integrity for every block from V001..V005. Per the locked design
-- decision (Phase H+ §Decisions, row 4) and the user's authorisation, we
-- TRUNCATE the chain and let `BlockchainService` recreate the genesis
-- block on next startup. There is no production data to preserve.
--
-- See `docs/phases/phase-h-plus.md` for the full plan.
--
-- TRUNCATE blocks CASCADE removes the rows in `transactions` too, since it
-- references blocks(index).

TRUNCATE TABLE blocks CASCADE;

ALTER TABLE blocks
    ADD COLUMN IF NOT EXISTS merkle_root TEXT NOT NULL DEFAULT '';

-- Drop the default once existing (now empty) rows are accounted for; new
-- inserts must supply the value explicitly.
ALTER TABLE blocks
    ALTER COLUMN merkle_root DROP DEFAULT;

INSERT INTO schema_migrations (version)
VALUES ('V006')
ON CONFLICT (version) DO NOTHING;
