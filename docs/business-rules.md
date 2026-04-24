# Business Rules — Blockchain Simulator

## 1. Scope

This document enumerates the business rules enforced by the Blockchain Simulator
at every layer: domain validation, protocol constraints, persistence guarantees,
and network behaviour.  Rules are referenced by identifier (BR-XXX) in code
comments and test cases.

---

## 2. Transaction Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-TX-01 | `amount` must be a positive number (> 0). Zero and negative values are rejected. | `domain/validation.py` · `migrations/versions/V003` (DB CHECK) |
| BR-TX-02 | `sender` must be a non-empty string (whitespace-only is invalid). | `domain/validation.py` |
| BR-TX-03 | `receiver` must be a non-empty string (whitespace-only is invalid). | `domain/validation.py` |
| BR-TX-04 | `sender` and `receiver` must differ. A self-transfer is rejected. | `domain/validation.py` |
| BR-TX-05 | All three fields (`sender`, `receiver`, `amount`) are mandatory. Missing any field results in HTTP 400. | `api/schemas.py` |
| BR-TX-06 | `amount` is stored with up to 8 decimal places (NUMERIC 20,8). | `migrations/versions/V003` |
| BR-TX-07 | A transaction is first validated, then appended to the **mempool** (pending state). It becomes confirmed only when included in a mined block. | `domain/mempool.py` · `basic-blockchain.py` (`_mine`) |
| BR-TX-08 | Transactions propagate to all registered peer nodes exactly once. A request carrying `X-Propagated: 1` is accepted into the local mempool but not re-forwarded. | `basic-blockchain.py` (transactions handler) · `domain/propagation.py` |

---

## 3. Block and Mining Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-BL-01 | The first block (genesis) has `index = 1`, `proof = 1`, `previous_hash = "0"`, and is created automatically on first initialisation. | `domain/blockchain.py` (`__init__`) |
| BR-BL-02 | The genesis block is never re-created if the repository already contains blocks. | `domain/blockchain.py` (`__init__`) · `test_integration_pg.py` |
| BR-BL-03 | Block index is auto-incremented: `new_index = last_block.index + 1`. | `domain/blockchain.py` (`create_block`) |
| BR-BL-04 | A valid proof satisfies: `SHA256(new_proof² − prev_proof²)` starts with `DIFFICULTY_PREFIX` (default `"00000"`). | `domain/blockchain.py` (`proof_of_work`, `is_chain_valid`) |
| BR-BL-05 | `DIFFICULTY_PREFIX` is configurable via the `DIFFICULTY_PREFIX` environment variable. Default is five leading zeros. | `config.py` |
| BR-BL-06 | A block's `previous_hash` must equal the SHA-256 hash of the preceding block (sorted-key JSON serialisation). | `domain/blockchain.py` (`is_chain_valid`) |
| BR-BL-07 | Mining flushes the entire mempool: all pending transactions are atomically included in the new block and removed from the mempool. | `basic-blockchain.py` (`_mine`) · `domain/mempool.py` (`flush`) |
| BR-BL-08 | Mining is rate-limited to **5 requests per 60 seconds** per process (sliding-window). Excess requests receive HTTP 429 with `Retry-After`. | `api/rate_limit.py` · `basic-blockchain.py` |
| BR-BL-09 | After a block is mined, all registered peers receive a `GET /api/v1/nodes/resolve` trigger (concurrent, fire-and-forget). | `domain/propagation.py` (`notify_resolve`) |
| BR-BL-10 | After a block is mined, all connected WebSocket clients receive `{"event": "block_mined", "block": {...}}`. | `api/websocket_hub.py` · `basic-blockchain.py` |

---

## 4. Chain Integrity Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-CH-01 | The chain is valid if and only if every block satisfies BR-BL-04 and BR-BL-06. | `domain/blockchain.py` (`is_chain_valid`) |
| BR-CH-02 | Tampering with any block (changing proof, previous_hash, or index) invalidates the chain from that block onward. | `domain/blockchain.py` (`is_chain_valid`) |
| BR-CH-03 | Chain replacement (consensus) is accepted only when the remote chain is strictly longer AND passes full validity checks. | `domain/consensus.py` (`resolve`) |
| BR-CH-04 | The average mining time is computed only when the chain contains at least 2 blocks. With only the genesis block, `avg_mine_time_seconds` is `null`. | `domain/blockchain.py` (`avg_mine_time_seconds`) |

---

## 5. Peer Node Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-ND-01 | Node URLs are normalised on registration: scheme is defaulted to `http://`, path and query components are stripped. Example: `"localhost:5001"` → `"http://localhost:5001"`. | `domain/node_registry.py` (`_normalise`) |
| BR-ND-02 | Duplicate node URLs (after normalisation) are silently ignored. A node appears at most once. | `domain/node_registry.py` · `infrastructure/postgres_node_registry.py` (ON CONFLICT DO NOTHING) |
| BR-ND-03 | Only `http://` and `https://` schemes are accepted for propagation and consensus requests. Other schemes are silently skipped (no error returned to caller). | `domain/propagation.py` · `domain/consensus.py` |
| BR-ND-04 | At least one URL must be provided in a `POST /api/v1/nodes/register` request. An empty `nodes` list results in HTTP 400. | `basic-blockchain.py` |

---

## 6. Consensus Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-CS-01 | Consensus follows the **longest valid chain** rule. The node with the most blocks is authoritative. | `domain/consensus.py` |
| BR-CS-02 | Remote chains that fail validity checks are discarded silently. A peer returning an invalid or unparseable chain does not halt local consensus. | `domain/consensus.py` (`_fetch_chain`) |
| BR-CS-03 | Network errors when fetching a peer's chain are silently ignored; that peer is skipped. | `domain/consensus.py` (`_fetch_chain`) |
| BR-CS-04 | If no peer has a longer valid chain, the local chain is kept and the response indicates `"replaced": false`. | `domain/consensus.py` · `basic-blockchain.py` |
| BR-CS-05 | Consensus resolution timeout per peer is 5 seconds. | `domain/consensus.py` (constructor default) |

---

## 7. Persistence Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-PS-01 | In-memory mode is the default. No environment variable or configuration is required to start the server. | `config.py` · `basic-blockchain.py` |
| BR-PS-02 | PostgreSQL mode is activated by providing a valid `DATABASE_URL` DSN. | `config.py` · `basic-blockchain.py` |
| BR-PS-03 | Migrations are idempotent. Re-running `migrate.py` on an up-to-date schema is a no-op. | `migrations/migrate.py` (`schema_migrations` tracking table) |
| BR-PS-04 | Each migration runs in its own transaction. A failure rolls back only that migration; already-applied versions are unaffected. | `migrations/migrate.py` (`_apply_file`) |
| BR-PS-05 | Deleting blocks cascades to their confirmed transactions (`ON DELETE CASCADE`). | `migrations/versions/V004` |
| BR-PS-06 | Mempool flush is atomic: all pending transactions are returned and deleted in a single database transaction. | `infrastructure/postgres_mempool_repository.py` (`flush`) |

---

## 8. API Contract Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-AP-01 | All error responses use the envelope `{"error": "<message>", "code": "<CODE>"}` with an appropriate HTTP status code. | `api/errors.py` |
| BR-AP-02 | Every request receives a `request_id` (UUID). It is taken from the `X-Request-ID` header if present, otherwise auto-generated. The same ID appears in all log entries for that request. | `basic-blockchain.py` (`before_request`) · `api/logging_config.py` |
| BR-AP-03 | The WebSocket endpoint `/api/v1/ws` is read-only for clients. Clients receive events; no client-to-server messaging is defined. | `api/websocket_hub.py` · `basic-blockchain.py` |
| BR-AP-04 | `GET /api/v1/health` returns HTTP 200 with `status: "ok"` when the DB is reachable (or not configured). It returns HTTP 503 with `status: "degraded"` when a configured DB is unreachable. | `basic-blockchain.py` · `api/health.py` |

---

## 9. Logging Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-LG-01 | All log entries are emitted as single-line JSON objects with fields: `ts`, `level`, `event`, `request_id`, `data`. | `api/logging_config.py` |
| BR-LG-02 | Exceptions include a `exc` field with the full traceback string. | `api/logging_config.py` (`_JSONFormatter`) |
