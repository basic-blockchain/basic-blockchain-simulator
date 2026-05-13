# Phase H+ — Block embeds transactions (Merkle-rooted)

**Status:** Active sprint — kicked off 2026-05-07.
**Tracks:** A (this repo: `basic-blockchain-simulator`) and B (`basic-blockchain-frontend`).
**Releases:** simulator **v0.10.0**, frontend **v0.6.0** (paired).

## Goal

Make `Block` carry its transactions as a first-class field, and make `hash_block` cover them via a Merkle root. After this phase, mutating a row in the `transactions` table after a block was mined will cause `is_chain_valid()` to return `False` — closing the integrity gap the v0.9.0 design left open.

## Decisions (locked, user-approved 2026-05-07)

| # | Decision | Outcome |
|---|----------|---------|
| 1 | Persistence | **Single source of truth: the `transactions` table.** `Block.transactions` is **hydrated at read time** via SQL JOIN. No JSONB column on `blocks`. |
| 2 | Hash construction | New `merkle_root` field on `Block`. `hash_block` covers it. Empty tx list yields a documented constant (`sha256("").hexdigest()`). |
| 3 | API shape | Block payload everywhere gains `merkle_root` + nested `transactions[]`. `POST /api/v1/mine_block` also keeps top-level `transactions` for back-compat (existing clients keep working without changes). |
| 4 | Migration | V006 `TRUNCATE blocks CASCADE` + add `merkle_root TEXT NOT NULL`. Genesis recreated at startup. Acceptable — no production data; user explicitly authorised. |
| 5 | Release order | Simulator v0.10.0 ships first. Frontend v0.6.0 ships against the new contract immediately after. |

## API contract (locked)

### `Block` payload (everywhere it appears)

```json
{
  "index": 2,
  "timestamp": "2026-05-07 12:34:56.789012",
  "proof": 12345,
  "previous_hash": "0000…",
  "merkle_root": "abcdef…",
  "transactions": [
    {"sender": "alice", "receiver": "bob", "amount": 5.0}
  ]
}
```

### `POST /api/v1/mine_block` response

Existing fields PLUS `merkle_root` PLUS the new `transactions` are nested under any block dict that ever ships in the response. The top-level `transactions` field is **kept** for back-compat:

```json
{
  "message": "A block is MINED",
  "index": 2,
  "timestamp": "...",
  "proof": 12345,
  "previous_hash": "...",
  "merkle_root": "...",
  "transactions": [ {"sender": "...", "receiver": "...", "amount": 1.0} ]
}
```

### `GET /api/v1/chain` response

Each entry in `chain` now includes `merkle_root` and a populated `transactions` list.

### `GET /api/v1/transactions`

**Unchanged** from v0.9.0 — still `{transactions: [{sender, receiver, amount, block_index, block_timestamp}], count}`.

### WebSocket `block_mined` event

`{event: "block_mined", block: <mining response dict>}` — same shape as the mining HTTP response (with `merkle_root` and top-level `transactions`).

## Track A — Simulator work items

| ID | Work item | Files |
|----|-----------|-------|
| H+A.0 | Commit this plan doc | `docs/phases/phase-h-plus.md` |
| H+A.1 | Add `transactions` and `merkle_root` to `Block`; private `_compute_merkle_root` helper | `domain/models.py`, `domain/blockchain.py` |
| H+A.2 | `BlockchainService.create_block(proof, previous_hash, transactions=None)` fills both fields | `domain/blockchain.py` |
| H+A.3 | `hash_block` covers `merkle_root`; `_validate_blocks` exercises the new path | `domain/blockchain.py` |
| H+A.4 | `_mine` flushes mempool then passes txs to `create_block` | `basic-blockchain.py` |
| H+A.5 | Migration V006 — TRUNCATE blocks CASCADE + add `merkle_root NOT NULL` | `migrations/versions/V006__block_merkle_root.sql` |
| H+A.6 | `PostgresBlockRepository.append` writes `merkle_root`; `get_all`/`last` hydrate `Block.transactions` via SQL JOIN. `InMemoryBlockRepository` keeps `Block.transactions` populated | `infrastructure/postgres_repository.py`, `domain/blockchain.py` |
| H+A.7 | Verify `chain_as_dicts` propagates new fields; HTTP integration | `basic-blockchain.py` |
| H+A.8 | Tests: Merkle determinism, tamper detection, PG integration, HTTP shape | `tests/test_phase_h_plus.py` |
| H+A.9 | Docs — README, `data-model.md`, `api-reference.md`, `business-rules.md`, `docs/releases/v0.10.0.md` | docs |
| H+A.10 | Push branch, open Draft PR against `develop` | gh pr |

## Track B — Frontend work items

| ID | Work item | Files |
|----|-----------|-------|
| H+B.0 | Commit this plan doc | `docs/phases/phase-h-plus.md` |
| H+B.1 | `Block` interface gains `merkleRoot: string` and `transactions: Transaction[]` | `src/domain/block.ts` |
| H+B.2 | `blockFromApi` maps `merkle_root → merkleRoot`, passes `transactions` through | `src/domain/block.ts` |
| H+B.3 | API parsers in `mining.ts`, `chain.ts`, `websocket.ts` parse new fields | `src/api/...` |
| H+B.4 | `chain` store accepts new shape; `confirmedTransactions.fetchConfirmed()` unchanged | `src/stores/...` |
| H+B.5 | Restore `transactions: []` and add `merkleRoot` in test `Block` literals | `tests/unit/components/organisms/MineButton.test.ts`, `tests/unit/composables/useBlockchainWs.test.ts` |
| H+B.6 | Surface `merkleRoot` in chain detail UI (short hash badge) | `src/views/ChainView.vue` (or organism) |
| H+B.7 | Coverage stays ≥ threshold; pre-existing failures fixed in same PR | tests |
| H+B.8 | Docs — README, CLAUDE.md, `docs/releases/v0.6.0.md`, bump `package.json` + lockfile to 0.6.0 | docs + package files |
| H+B.9 | Verification — `npm run lint`, `npm run typecheck`, `npm test -- --run` all green | local |
| H+B.10 | Push branch, open Draft PR against `develop` | gh pr |

## Traceability

Every commit subject ends with `[H+A.x]` or `[H+B.x]` in brackets. Where a logical change naturally spans multiple work items in the same file, the suffix lists all IDs (e.g., `[H+A.1, H+A.2, H+A.3]`). Each PR description checklists every ID it carries.

The sprint completes when:
- `feat/block-embeds-transactions-domain` is merged into simulator `develop` and promoted through GitFlow to `main`, then tagged `v0.10.0`.
- `feat/block-embeds-transactions-frontend` is merged into frontend `develop` and promoted through GitFlow to `main`, then tagged `v0.6.0`.
- Release notes for both versions reference this plan doc.
