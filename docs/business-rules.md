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
| BR-AU-08 | `POST /auth/register` accepts an optional `country` field; values are case-folded to uppercase and validated as an ISO 3166-1 alpha-2 (two alphabetic characters). Anything else returns `VALIDATION_ERROR`. The value is persisted on `users.country` and surfaces on `/admin/users`. | `api/auth_routes.py` (`register`) · `domain/user_repository.py` (`create_user`) |
| BR-AU-09 | `POST /auth/login` stamps `users.last_active = now()` **after** every credential / activation / ban guard so failed attempts never update the column (no enumeration leak via the timestamp). | `api/auth_routes.py` (`login`) · `users.touch_last_active(...)` |

---

## 6. Roles, Permissions & Audit Rules *(Phase I.2, v0.12.0)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-RB-01 | A protected route declares its requirement with `@require_permission(Permission.X)`. The decorator aborts with HTTP 401 / `AUTH_REQUIRED` when there is no authenticated user, and HTTP 403 / `FORBIDDEN` when the authenticated user lacks the permission. | `api/permissions.py` (`require_permission`) |
| BR-RB-02 | Permission resolution is 3-level: (1) per-user grants in `user_permissions`, (2) per-role overrides in `role_permissions`, (3) hardcoded baseline in `ROLE_PERMISSIONS`. The first match short-circuits. | `domain/permissions.py` (`has_permission`) |
| BR-RB-03 | When a `role_permissions` row exists for a role, it **replaces** the hardcoded baseline for that role rather than augmenting it. A row that lists fewer permissions than the baseline therefore reduces the role's surface — which is the whole point of the override table. | `domain/permissions.py` (`has_permission`) |
| BR-RB-04 | ADMIN's hardcoded baseline covers the user/role/permission management cluster (`CREATE_USER`, `VIEW_USERS`, `UPDATE_USER`, `BAN_USER`, `UNBAN_USER`, `DELETE_USER`, `RESTORE_USER`, `ASSIGN_ROLE`, `MANAGE_PERMISSIONS`, `VIEW_AUDIT_LOG`) plus wallet oversight (`VIEW_WALLETS`, `FREEZE_WALLET`, `UNFREEZE_WALLET`) and the admin's own wallet ops (`CREATE_WALLET`, `TRANSFER`). `MINT` and `VIEW_TRANSFERS` are **not** in ADMIN's baseline — they require an explicit per-admin grant via `POST /admin/users/<self>/permissions`. The grant is audited. | `domain/permissions.py` (`ROLE_PERMISSIONS`) |
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
| BR-WL-09 | The legacy `POST /api/v1/transactions` endpoint (v0.10.0 unauthenticated path) keeps working for back-compat with older clients, but its transactions never move balances — they are recorded in chain history with empty wallet IDs and skipped by `apply_block_deltas`. Phase I.4 switched the frontend to `/transactions/signed`; the legacy path remains deprecated. | `domain/wallet.py` (`apply_block_deltas`), `api/wallet_routes.py` |
| BR-CH-03 | Chain replacement (consensus) is accepted only when the remote chain is strictly longer AND passes full validity checks. | `domain/consensus.py` (`resolve`) |
| BR-CH-04 | The average mining time is computed only when the chain contains at least 2 blocks. With only the genesis block, `avg_mine_time_seconds` is `null`. | `domain/blockchain.py` (`avg_mine_time_seconds`) |

---

## 8. Admin Enrichment Rules *(Phase I.5, v0.14.0)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-AD-01 | `DELETE /admin/users/<id>` soft-deletes a user by setting `users.deleted_at` and freezes all their wallets. The action is rejected if the user does not exist, is already deleted, or the actor is deleting themselves. | `api/admin_routes.py`, `infrastructure/postgres_user_store.py` |
| BR-AD-02 | `POST /admin/users/<id>/restore` clears `deleted_at` and, by default, unfreezes the user's wallets (`unfreeze_wallets` defaults to true). | `api/admin_routes.py` |
| BR-AD-03 | `PATCH /admin/users/<id>` updates `display_name` and/or `email` (both <= 255 chars). Email uniqueness is enforced; invalid inputs return `VALIDATION_ERROR`. | `api/admin_routes.py`, `infrastructure/postgres_user_store.py` |
| BR-AD-04 | `GET /admin/wallets` lists all wallets with owner metadata (username, display_name) and current freeze state. | `api/admin_routes.py` |
| BR-AD-05 | `POST /admin/wallets/<id>/freeze` and `/unfreeze` toggle wallet state and write audit entries (`WALLET_FROZEN` / `WALLET_UNFROZEN`). | `api/admin_routes.py`, `domain/audit.py` |

---

## 8d. Dashboard Aggregation Rules *(Phase 6e — contracts)*

These rules pin the shape and semantics of the Phase 6e dashboard
endpoints (`/admin/volume`, `/admin/stats?compare=`, `/admin/audit?severity=`,
`/admin/movements/top`) before any implementation lands. Codes
referenced: `RANGE_INVALID`, `COMPARE_INVALID`, `SEVERITY_INVALID`.

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-AD-06 | USD-equivalent amounts on the dashboard endpoints use the exchange rate **as of the transaction's `confirmed_at`**, never the current rate. Historical charts must not shift retroactively when rates move. The literal target currency is `config.DASHBOARD_QUOTE_CURRENCY` (default `USDT`; Phase 6i.1) — response fields keep the `balance_usd`/`amount_usd`/`volume_usd` names because stablecoins peg 1:1 with USD. | `/admin/volume`, `/admin/movements/top` (implementation) |
| BR-AD-07 | Transactions whose pair has no rate at `confirmed_at` are **excluded** from USD-aggregated totals — never silently zeroed. `/admin/volume` surfaces them as `unpriced_count` (per-bucket and per-totals); `/admin/movements/top` drops them from the ranked list. | `/admin/volume`, `/admin/movements/top` |
| BR-AD-08 | `/admin/volume`: `range ∈ {30d, 90d, 1y}` (otherwise `RANGE_INVALID`). `bucket ∈ {day, week}` and defaults to `day` for `30d`, `week` otherwise. Empty buckets are emitted with `volume_usd: "0"` and `tx_count: 0` so the client can render a continuous axis without back-filling. | `/admin/volume` |
| BR-AD-09 | `/admin/stats?compare=`: `compare ∈ {7d, 30d}` (otherwise `COMPARE_INVALID`). The response adds a `compare` block with `delta_abs` and `delta_pct` per metric. `delta_pct` is `null` when the previous-period value is `0` (no `Infinity`, no sentinel). Without `?compare=` the response shape is unchanged. | `/admin/stats` |
| BR-AD-10 | Audit `severity` is derived **server-side** from the action constant and is canonical: clients must not reclassify. Mapping: `critical` → `USER_BANNED`, `USER_DELETED`, `WALLET_FROZEN`, `MINT`, `KYC_DOCUMENT_REJECTED`. `warning` → `TEMP_PASSWORD_ISSUED`, `PASSWORD_CHANGED`, `ROLE_GRANTED`, `ROLE_REVOKED`, `PERMISSION_GRANTED`, `PERMISSION_REVOKED`, `ROLE_PERMISSION_GRANTED`, `ROLE_PERMISSION_REVOKED`, `KYC_LEVEL_PROMOTED`. `info` → everything else. | `domain/audit.py` (new `SEVERITY` map) |
| BR-AD-11 | `/admin/audit?severity=`: `severity ∈ {critical, warning, info}` (otherwise `SEVERITY_INVALID`). `since ∈ {1h, 24h, 7d, 30d}`; both filters compose with the existing `action`/`actor_id`/`target_id`/`limit` params. | `/admin/audit` |
| BR-AD-12 | `/admin/movements/top`: `range ∈ {24h, 7d, 30d}` (default `24h`); `limit ∈ [1, 50]` (default `10`). Movements are ordered by `amount_usd` descending. Excluded (unpriced) transfers do not surface in the ranked list nor count toward `total_volume_usd`. | `/admin/movements/top` |
| BR-AD-13 | Phase 6i.1 — the simulator seeds a default currencies catalog (`USDT, USDC, BTC, ETH, SOL, NATIVE`) plus mid-market X/USDT rates on first boot via `infrastructure.dashboard_seed.bootstrap_dashboard_seed(...)`. The seed is idempotent (existing currencies and existing rows for the same pair are skipped — operator-set rates are never overwritten) and disabled when `DASHBOARD_BOOTSTRAP_SEED=false` or `TESTING=true`. | `infrastructure/dashboard_seed.py`, `basic-blockchain.py` (`create_app`) |

---

## 8b. KYC User-Flow Rules *(Phase 6g)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-KY-01 | Document `key` must be one of `{dni, selfie, address, funds}`. Anything else returns `KYC_UNKNOWN_DOCUMENT_KEY`. | `api/kyc_routes.py` (`ALLOWED_DOC_KEYS`) |
| BR-KY-02 | Upload payload requires non-empty `data` (base64), `filename`, and `content_type`. Missing or empty fields return `KYC_INVALID_DOCUMENT_DATA` / `VALIDATION_ERROR`. | `api/kyc_routes.py` |
| BR-KY-03 | The raw base64 `data` is persisted in `users.kyc_documents` but **never** returned by the API. `_public_document` whitelists the safe fields (`key`, `status`, `uploaded_at`, `reviewed_at`, `reject_reason`, `content_type`, `filename`). | `api/kyc_routes.py` |
| BR-KY-04 | A review can only target the next level: `target` must equal `current_level + 1`. Skipping levels returns `KYC_LEVEL_SKIP_NOT_ALLOWED`. | `api/kyc_routes.py` (`LEVEL_ORDER`) |
| BR-KY-05 | Required documents per target: `L1 → {dni, selfie}`, `L2 → +address`, `L3 → +funds`. Missing documents return `KYC_MISSING_DOCUMENTS` with the list of missing keys. | `api/kyc_routes.py` (`REQUIRED_DOCS_FOR`) |
| BR-KY-06 | While `users.kyc_pending_review IS NOT NULL` no further uploads or review submissions are accepted from the same user — both return `KYC_REVIEW_IN_PROGRESS`. The flag is cleared by the admin approve-all + promote flow on success, or by any admin document rejection (see BR-KY-12 / BR-KY-13). | `api/kyc_routes.py` |
| BR-KY-07 | Successful upload emits audit `KYC_DOCUMENT_UPLOADED` with `{key, content_type, filename}`. Successful review submission emits `KYC_REVIEW_REQUESTED` with `{target}`. | `api/kyc_routes.py`, `domain/audit.py` |
| BR-KY-08 | All three routes (`/me/kyc/status`, `/documents`, `/review`) gate on `require_auth()`; no extra permission is needed — any authenticated user manages **their own** KYC state and cannot read or mutate anyone else's. | `api/auth_middleware.py`, `api/kyc_routes.py` |

---

## 8c. KYC Admin-Review Rules *(Phase 6g-admin)*

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-KY-09 | All four admin routes (`/admin/kyc/pending`, `.../approve`, `.../reject`, `.../promote`) gate on `Permission.REVIEW_KYC`. The permission ships in the ADMIN baseline; granting it to another role or to a single user follows the standard override path. | `api/kyc_admin_routes.py`, `domain/permissions.py` |
| BR-KY-10 | Approve, reject and promote require the target user to have `kyc_pending_review IS NOT NULL`; otherwise the call fails with `KYC_NO_PENDING_REVIEW`. | `api/kyc_admin_routes.py` |
| BR-KY-11 | Approve flips a single document to `status: 'verified'` and stamps `reviewed_at`. It does **not** clear pending review state — the operator must approve every required doc and then call `/promote`. | `api/kyc_admin_routes.py` |
| BR-KY-12 | Reject requires a non-empty `reason`, flips the document to `status: 'rejected'` with `reject_reason`, and aborts the whole review by clearing `kyc_pending_review` and `kyc_submitted_at` so the user can re-upload without hitting `KYC_REVIEW_IN_PROGRESS`. | `api/kyc_admin_routes.py` |
| BR-KY-13 | Promote requires that every document in `REQUIRED_DOCS_FOR[target]` is in `status: 'verified'`; otherwise it fails with `KYC_NOT_ALL_DOCUMENTS_VERIFIED` listing the keys still pending. On success it sets `kyc_level` to the target and clears the pending review state. | `api/kyc_admin_routes.py` |
| BR-KY-14 | Document keys on the admin routes are validated against the same `ALLOWED_DOC_KEYS` set as the user flow; unknown keys return `KYC_UNKNOWN_DOCUMENT_KEY`. A key that was never uploaded returns `KYC_DOCUMENT_NOT_UPLOADED`. | `api/kyc_admin_routes.py` |
| BR-KY-15 | Audit actions emitted: approve → `KYC_DOCUMENT_APPROVED {key, target_user_id}`; reject → `KYC_DOCUMENT_REJECTED {key, target_user_id, reason}`; promote → `KYC_LEVEL_PROMOTED {from_level, to_level, target_user_id}`. | `domain/audit.py`, `api/kyc_admin_routes.py` |
| BR-KY-16 | The raw base64 `data` payload is **never** exposed on the admin surface either — `/admin/kyc/pending` reuses `_public_document` to keep the contract identical to the user side. | `api/kyc_admin_routes.py` |

---

## 8e. Treasury Dual-Sign Rules *(Phase 7.8 — contracts)*

These rules pin the shape and semantics of the Phase 7.8 treasury
dual-sign envelope (`/admin/treasury/distribute/*` and the
threshold-gated dual-sign extension of `/admin/mint`) **before** any
implementation lands. DISTRIBUTE and MINT live in **separate tables**
(`treasury_distributions`, `treasury_mint_ops`) with **separate
endpoints** — see §11 of the spec for the rationale. The full
contract — endpoint shapes, request / response payloads, state
machine, schema and migration plan — lives in
[`docs/specs/7.8.0-treasury-dual-sign.md`](specs/7.8.0-treasury-dual-sign.md).
The rule rows below are stubs; the full text per rule lands with the
code in sub-phases 7.8.1 → 7.8.7. New error codes referenced:
`SAME_SIGNER_FORBIDDEN`, `DISTRIBUTION_NOT_FOUND`,
`DISTRIBUTION_NOT_PENDING`, `MINT_OP_NOT_FOUND`,
`MINT_OP_NOT_PENDING`, `NOT_INITIATOR`, `WALLET_NOT_TREASURY`,
`CURRENCY_MISMATCH`, `RECIPIENTS_EMPTY`, `RECIPIENTS_DUPLICATE`,
`RECIPIENT_NOT_FOUND`, `RECIPIENT_NO_WALLET`, `INSUFFICIENT_FUNDS`.

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-TR-01 | `SAME_SIGNER_FORBIDDEN` — approver `user_id` must differ from `initiated_by`, enforced both at the service layer **and** by a `CHECK` constraint on each table (`chk_dist_same_signer` on `treasury_distributions`, `chk_mint_same_signer` on `treasury_mint_ops`). | `services/treasury_distribution_service.py`, `services/mint_service.py`, migrations `V021`/`V022` |
| BR-TR-02 | Distribution recipients must each own a non-treasury wallet in the op currency; missing → `RECIPIENT_NO_WALLET`. | `services/treasury_distribution_service.py` |
| BR-TR-03 | Duplicate ids in `recipient_user_ids` return `RECIPIENTS_DUPLICATE`; the service never silently de-duplicates. | `services/treasury_distribution_service.py` |
| BR-TR-04 | Approval is **atomic** with execution: the op transitions to `executed` in the same critical section that submits the N transfers / the mint. Partial execution is forbidden — any submission failure rolls back prior submissions and leaves the op `pending_approval`. | `services/treasury_distribution_service.py`, `services/mint_service.py` |
| BR-TR-05 | `executed` and `cancelled` are terminal — any further mutation returns `DISTRIBUTION_NOT_PENDING` / `MINT_OP_NOT_PENDING` (per entity). | `services/treasury_distribution_service.py`, `services/mint_service.py` |
| BR-TR-06 | Only the initiator can cancel a pending op (`NOT_INITIATOR` otherwise); approval can come from any actor with the per-entity approve permission other than the initiator. | `services/treasury_distribution_service.py`, `services/mint_service.py` |
| BR-TR-07 | Mint threshold: when `amount >= MINT_DUAL_SIGN_THRESHOLD` (env var, default `0` = disabled), `POST /admin/mint` returns 202 + op_id and writes a row into `treasury_mint_ops`. Below threshold the route preserves today's synchronous 201 response bit-for-bit and writes no row. | `api/wallet_routes.py`, `services/mint_service.py` |
| BR-TR-08 | Supply conservation: a distribution emits exactly `N` transfers of `amount_per_wallet` each (totalling `N * amount_per_wallet`); a dual-sign mint emits exactly one coinbase identical to today's mint. The envelope adds zero supply, only gating. | `services/treasury_distribution_service.py`, `services/mint_service.py`, supply-conservation tests |
| BR-TR-09 | Audit lifecycle per entity: distributions → `TREASURY_DISTRIBUTION_{INITIATED,APPROVED,EXECUTED,CANCELLED}`; mint ops → `TREASURY_MINT_OP_{INITIATED,APPROVED,EXECUTED,CANCELLED}`. Severity (BR-AD-10): both `*_EXECUTED` → `critical`; every `*_INITIATED`, `*_APPROVED`, `*_CANCELLED` → `warning`. The existing synchronous `MINT` action is unchanged. | `domain/audit.py` |
| BR-TR-10 | `GET /admin/treasury/distribute` and `GET /admin/mint/operations` each return only their own entity to ADMIN and OPERATOR (read-only); no per-row scoping in this phase. | `api/admin_routes.py`, `api/wallet_routes.py` |

---

## 9. Peer Node Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-ND-01 | Node URLs are normalised on registration: scheme is defaulted to `http://`, path and query components are stripped. Example: `"localhost:5001"` → `"http://localhost:5001"`. | `domain/node_registry.py` (`_normalise`) |
| BR-ND-02 | Duplicate node URLs (after normalisation) are silently ignored. A node appears at most once. | `domain/node_registry.py` · `infrastructure/postgres_node_registry.py` (ON CONFLICT DO NOTHING) |
| BR-ND-03 | Only `http://` and `https://` schemes are accepted for propagation and consensus requests. Other schemes are silently skipped (no error returned to caller). | `domain/propagation.py` · `domain/consensus.py` |
| BR-ND-04 | At least one URL must be provided in a `POST /api/v1/nodes/register` request. An empty `nodes` list results in HTTP 400. | `basic-blockchain.py` |

---

## 10. Consensus Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-CS-01 | Consensus follows the **longest valid chain** rule. The node with the most blocks is authoritative. | `domain/consensus.py` |
| BR-CS-02 | Remote chains that fail validity checks are discarded silently. A peer returning an invalid or unparseable chain does not halt local consensus. | `domain/consensus.py` (`_fetch_chain`) |
| BR-CS-03 | Network errors when fetching a peer's chain are silently ignored; that peer is skipped. | `domain/consensus.py` (`_fetch_chain`) |
| BR-CS-04 | If no peer has a longer valid chain, the local chain is kept and the response indicates `"replaced": false`. | `domain/consensus.py` · `basic-blockchain.py` |
| BR-CS-05 | Consensus resolution timeout per peer is 5 seconds. | `domain/consensus.py` (constructor default) |

---

## 11. Persistence Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-PS-01 | In-memory mode is the default. No environment variable or configuration is required to start the server. | `config.py` · `basic-blockchain.py` |
| BR-PS-02 | PostgreSQL mode is activated by providing a valid `DATABASE_URL` DSN. | `config.py` · `basic-blockchain.py` |
| BR-PS-03 | Migrations are idempotent. Re-running `migrate.py` on an up-to-date schema is a no-op. | `migrations/migrate.py` (`schema_migrations` tracking table) |
| BR-PS-04 | Each migration runs in its own transaction. A failure rolls back only that migration; already-applied versions are unaffected. | `migrations/migrate.py` (`_apply_file`) |
| BR-PS-05 | Deleting blocks cascades to their confirmed transactions (`ON DELETE CASCADE`). | `migrations/versions/V004` |
| BR-PS-06 | Mempool flush is atomic: all pending transactions are returned and deleted in a single database transaction. | `infrastructure/postgres_mempool_repository.py` (`flush`) |

---

## 12. API Contract Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-AP-01 | All error responses use the envelope `{"error": "<message>", "code": "<CODE>"}` with an appropriate HTTP status code. | `api/errors.py` |
| BR-AP-02 | Every request receives a `request_id` (UUID). It is taken from the `X-Request-ID` header if present, otherwise auto-generated. The same ID appears in all log entries for that request. | `basic-blockchain.py` (`before_request`) · `api/logging_config.py` |
| BR-AP-03 | The WebSocket endpoint `/api/v1/ws` is read-only for clients. Clients receive events; no client-to-server messaging is defined. | `api/websocket_hub.py` · `basic-blockchain.py` |
| BR-AP-04 | `GET /api/v1/health` returns HTTP 200 with `status: "ok"` when the DB is reachable (or not configured). It returns HTTP 503 with `status: "degraded"` when a configured DB is unreachable. | `basic-blockchain.py` · `api/health.py` |

---

## 13. Logging Rules

| ID | Rule | Enforcement layer |
|----|------|-------------------|
| BR-LG-01 | All log entries are emitted as single-line JSON objects with fields: `ts`, `level`, `event`, `request_id`, `data`. | `api/logging_config.py` |
| BR-LG-02 | Exceptions include a `exc` field with the full traceback string. | `api/logging_config.py` (`_JSONFormatter`) |
