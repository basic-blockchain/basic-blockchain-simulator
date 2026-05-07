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
| BR-BL-11 | Each block carries a `merkle_root` (SHA-256 binary Merkle tree, Bitcoin-style odd-level duplication) computed over its `transactions` list at mining time. Empty blocks use `EMPTY_MERKLE_ROOT = sha256("").hexdigest()`. *(v0.10.0)* | `domain/blockchain.py` (`_compute_merkle_root`, `create_block`) |
| BR-BL-12 | A block's transactions are persisted in the same DB transaction that writes the block row, so a Merkle root never references rows that are missing from the `transactions` table. *(v0.10.0)* | `infrastructure/postgres_repository.py` (`append`) |

---

## 4. Chain Integrity Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-CH-01 | The chain is valid if and only if every block satisfies BR-BL-04, BR-BL-06, and BR-CH-05. | `domain/blockchain.py` (`is_chain_valid`) |
| BR-CH-02 | Tampering with any block (changing proof, previous_hash, index, merkle_root, or any of its transactions) invalidates the chain. | `domain/blockchain.py` (`is_chain_valid`) |
| BR-CH-05 | Every block's stored `merkle_root` must equal the Merkle root recomputed from its `transactions` list; the chain hash also covers `merkle_root`, so a mutated transaction or a re-stamped Merkle root both fail validation. *(v0.10.0)* | `domain/blockchain.py` (`_validate_blocks`) |

---

## 5. Authentication & Identity Rules *(Phase I.1, v0.11.0)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-AU-01 | `users.username` is unique and 1..64 characters; `users.user_id` is a server-generated 32-hex string used as the JWT `sub` claim. | `migrations/V007` · `api/auth_routes.py` (`register`) |
| BR-AU-02 | A newly registered user has `password_hash = ''` and an `activation_code`; login is denied until `POST /auth/activate` consumes the code and sets a bcrypt hash. Passwords must be at least 8 characters. | `api/auth_routes.py` (`register`, `activate`) |
| BR-AU-03 | Login uniformly returns HTTP 400 with `code: AUTH_INVALID_CREDENTIALS` for missing user, wrong password, not-yet-activated account, AND banned account, to prevent account enumeration. | `api/auth_routes.py` (`login`) |
| BR-AU-04 | The first registered user whose username matches `BOOTSTRAP_ADMIN_USERNAME` (env var) is auto-promoted to `ADMIN`. Every other registration receives the default role `VIEWER`. The promotion only triggers when `users` is empty — later same-username registrations get the default role. | `api/auth_routes.py` (`register`) |
| BR-AU-05 | JWTs are HS256 with a `{sub, roles, iat, exp}` payload and a configurable TTL (`JWT_TTL_SECONDS`, default 1800). Tampered tokens fail with HTTP 401 / `AUTH_INVALID_TOKEN`; expired tokens fail with HTTP 401 / `AUTH_EXPIRED_TOKEN`. | `domain/auth.py` (`create_jwt`/`decode_jwt`) · `api/auth_middleware.py` |
| BR-AU-06 | The simulator refuses to start when `JWT_SECRET` is empty unless `TESTING=true` is set. The test suite uses a deterministic 38-byte sentinel so unit tests are reproducible without environment coupling. | `config.py` · `basic-blockchain.py` (`create_app`) |
| BR-AU-07 | The middleware never persists the decoded token; it lives only on `g.current_user` for the duration of the request. Public endpoints (`/`, `/health`, `/chain`, `/valid`, `/auth/*`, legacy `/get_chain`/`/valid`) reach the route with `g.current_user = None`. | `api/auth_middleware.py` (`PUBLIC_PATHS`) |

---

## 6. Roles, Permissions & Audit Rules *(Phase I.2, v0.12.0)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-RB-01 | A protected route declares its requirement with `@require_permission(Permission.X)`. The decorator aborts with HTTP 401 / `AUTH_REQUIRED` when there is no authenticated user, and HTTP 403 / `FORBIDDEN` when the authenticated user lacks the permission. | `api/permissions.py` (`require_permission`) |
| BR-RB-02 | Permission resolution is 3-level: (1) per-user grants in `user_permissions`, (2) per-role overrides in `role_permissions`, (3) hardcoded baseline in `ROLE_PERMISSIONS`. The first match short-circuits. | `domain/permissions.py` (`has_permission`) |
| BR-RB-03 | When a `role_permissions` row exists for a role, it **replaces** the hardcoded baseline for that role rather than augmenting it. A row that lists fewer permissions than the baseline therefore reduces the role's surface — which is the whole point of the override table. | `domain/permissions.py` (`has_permission`) |
| BR-RB-04 | ADMIN's hardcoded baseline covers the user/role/permission management cluster (`CREATE_USER`, `VIEW_USERS`, `UPDATE_USER`, `BAN_USER`, `UNBAN_USER`, `ASSIGN_ROLE`, `MANAGE_PERMISSIONS`, `VIEW_AUDIT_LOG`) plus the admin's own wallet ops (`CREATE_WALLET`, `TRANSFER`). Financial-action permissions (`MINT`, `FREEZE_WALLET`, `UNFREEZE_WALLET`) and cross-user data visibility (`VIEW_WALLETS`, `VIEW_TRANSFERS`) are **not** in ADMIN's baseline — they require an explicit grant per admin via `POST /admin/users/<self>/permissions`. The grant is audited. | `domain/permissions.py` (`ROLE_PERMISSIONS`) |
| BR-RB-05 | OPERATOR is "audit-light" by default: own wallet ops + cross-user read of wallets and transfers (`VIEW_WALLETS`, `VIEW_TRANSFERS`). VIEWER is the most-restricted role and the default at registration: own wallet ops only (`CREATE_WALLET`, `TRANSFER`). | `domain/permissions.py` (`ROLE_PERMISSIONS`) |
| BR-RB-06 | Every state-mutating admin action (grant/revoke role, ban/unban user, grant/revoke permission) writes a row to `audit_log` with the actor, action, target, JSONB details, and a server-side `created_at`. The audit log is append-only by convention — no UPDATE / DELETE. | `domain/audit.py`, `api/admin_routes.py`, `infrastructure/postgres_user_store.py` |
| BR-RB-07 | An admin cannot ban themselves; the ban endpoint returns HTTP 400 / `SELF_ACTION_FORBIDDEN` instead. This guarantees that at least the actor remains logged in to undo a wrong action. | `api/admin_routes.py` (`ban_user`) |
| BR-RB-08 | A banned account is rejected at login with the uniform `AUTH_INVALID_CREDENTIALS` (BR-AU-03). Existing JWTs remain valid until expiry — server-side session revocation on ban is out of scope for v0.12.0. | `api/auth_routes.py` (`login`) |

---

## 7. Wallet & Transfer Rules *(Phase I.3, v0.13.0)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-WL-01 | Each wallet owns a secp256k1 keypair derived from a 12-word BIP-39 mnemonic generated at creation. The server stores **only** `wallets.public_key`; the mnemonic is returned once in the response of `POST /api/v1/wallets` and is never persisted, logged, or retrievable again. | `domain/wallet.py` (`WalletService.create_wallet`) |
| BR-WL-02 | A wallet's owner is fixed by `wallets.user_id` (FK to `users.user_id`). The signed-transfer endpoint rejects (`WALLET_OWNERSHIP`) any request whose `sender_wallet_id` belongs to a user other than the JWT subject. | `api/wallet_routes.py` (`submit_signed_transaction`) |
| BR-WL-03 | A transfer is admitted only when (a) both wallets exist, (b) neither is frozen, (c) sender has at least `amount`, (d) the ECDSA signature verifies against `sender.public_key`, and (e) the nonce is strictly greater than `wallet_nonces.last_used_nonce` for that wallet. The first failure short-circuits with a stable error code (`WALLET_NOT_FOUND`, `WALLET_FROZEN`, `INSUFFICIENT_BALANCE`, `SIGNATURE_INVALID`, `NONCE_REPLAY`). | `domain/wallet.py` (`TransferService`), `api/wallet_routes.py` |
| BR-WL-04 | The signed payload is the UTF-8 bytes of `f"{sender_wallet_id}:{receiver_wallet_id}:{amount}:{nonce}"`. `amount` uses plain decimal notation (no exponent), `nonce` is a positive integer. Both client and server MUST use exactly this format. | `domain/crypto.py` (`canonical_transfer_message`) |
| BR-WL-05 | Wallet balances change **only when a block is mined**: `_mine` calls `apply_block_deltas(wallets, included_txs)`. Coinbase / mint transactions credit the receiver; transfers debit sender + credit receiver in one DB transaction. Pre-Phase-I.3 legacy transactions (empty `sender_wallet_id`) are recorded in chain history but cause no balance change. | `basic-blockchain.py` (`_mine`), `domain/wallet.py` (`apply_block_deltas`) |
| BR-WL-06 | `is_chain_valid()` re-verifies every non-coinbase transaction's signature against the sender wallet's stored `public_key` on every call. A tampered `transactions.amount`, `nonce`, or `signature` therefore makes validity flip to `false` even though the row was admitted at confirmation time. | `domain/blockchain.py` (`_validate_blocks`) |
| BR-WL-07 | The only way to introduce new coin into the chain is `POST /api/v1/admin/mint`, gated by the `MINT` permission. `MINT` is **not** in the ADMIN baseline (BR-RB-04); an admin must self-grant it via `MANAGE_PERMISSIONS` first, and the grant lands in `audit_log`. The mint produces a transaction with `signature == "MINT"`. | `api/wallet_routes.py` (`admin_mint`), `domain/wallet.py` (`MintService`) |
| BR-WL-08 | Total supply is conserved: `sum(wallets.balance) == sum(amount of every coinbase transaction)` at every chain height, regardless of how many transfers happened. Tests in `tests/test_supply_conservation.py` assert this across one transfer, many transfers in one block, and many transfers across many blocks. | `domain/wallet.py` (`apply_block_deltas`), `tests/test_supply_conservation.py` |
| BR-WL-09 | The legacy `POST /api/v1/transactions` endpoint (v0.10.0 unauthenticated path) keeps working in v0.13.0 for back-compat with the v0.6.0 frontend, but its transactions never move balances — they are recorded in chain history with empty wallet IDs and skipped by `apply_block_deltas`. Phase I.4 frontend will switch to `/transactions/signed` and the legacy path becomes deprecated. | `domain/wallet.py` (`apply_block_deltas`), `api/wallet_routes.py` |
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
