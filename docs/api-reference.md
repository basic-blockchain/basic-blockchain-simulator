# API Reference — Blockchain Simulator v1

**Base URL:** `http://localhost:5000/api/v1`  
**Content-Type:** `application/json` (all requests and responses)  
**ASGI server:** Quart 0.19+ (development: built-in; production: Hypercorn)

---

## Error Envelope

All error responses share a common structure:

```json
{
  "error": "Human-readable message",
  "code":  "MACHINE_READABLE_CODE"
}
```

| HTTP Status | Code | Meaning |
|-------------|------|---------|
| 400 | `VALIDATION_ERROR` | Request body failed schema or business-rule validation |
| 400 | `BAD_REQUEST` | Generic bad request |
| 404 | `NOT_FOUND` | Route does not exist |
| 405 | `METHOD_NOT_ALLOWED` | Wrong HTTP method for this route |
| 429 | `RATE_LIMITED` | Mining rate limit exceeded |
| 500 | `INTERNAL_ERROR` | Unhandled server error |
| 503 | `DEGRADED` | Database unreachable (health endpoint only) |

---

## Request Correlation

Every request may include `X-Request-ID: <uuid>`. If omitted, the server
generates a UUID4 and uses it for logging. The value is **not** echoed in
the response; check server logs for correlation.

---

## Authentication (Phase I.1, v0.11.0)

The simulator issues Bearer JWTs (HS256, default 30-minute TTL). Routes
that need authentication call `require_auth()` or are wrapped with
`@require_permission(...)`. Public routes skip token parsing and include
`/`, `/api/v1/health`, `/api/v1/chain`, `/api/v1/valid`, and
`/api/v1/auth/*`. The middleware rejects malformed or expired tokens with
401 and attaches `current_user` to the request context when valid.

The following error codes are specific to the auth flow:

| HTTP | Code | When |
|------|------|------|
| 400 | `VALIDATION_ERROR` | Bad / missing JSON fields |
| 400 | `USERNAME_TAKEN` / `EMAIL_TAKEN` | Duplicate identity at register |
| 400 | `AUTH_INVALID_ACTIVATION` | Wrong username / activation code pair |
| 400 | `AUTH_INVALID_CREDENTIALS` | Login: missing user, wrong password, or not yet activated. **Single code on purpose** to avoid account enumeration. |
| 401 | `AUTH_REQUIRED` | Protected route reached without a token |
| 401 | `AUTH_INVALID_TOKEN` | Malformed / forged / wrong-signature token |
| 401 | `AUTH_EXPIRED_TOKEN` | Token's `exp` is in the past |
| 401 | `AUTH_USER_NOT_FOUND` | `/me` resolves a user_id no longer in DB |

### POST /api/v1/auth/register

**Request body**
```json
{ "username": "alice", "display_name": "Alice", "email": "alice@example.com" }
```

`display_name` defaults to `username` when omitted. `email` is optional.

**Response 201**
```json
{
  "message": "User registered. Use the activation code to set your password.",
  "user_id": "b4ff…d7",
  "username": "alice",
  "activation_code": "8VKQ2YQ7B3HXCZNT"
}
```

The activation code is shown **only on this response**. The new account
has no password until `POST /auth/activate` is called.

### POST /api/v1/auth/activate

**Request body**
```json
{ "username": "alice", "activation_code": "8VKQ2YQ7B3HXCZNT", "password": "hunter12345" }
```

Password must be at least 8 characters. The code is consumed (set to
`NULL`) and `activated_at` stamped to `now()`.

**Response 200**
```json
{ "message": "Account activated. You can now log in.", "user_id": "b4ff…d7" }
```

### POST /api/v1/auth/login

**Request body**
```json
{ "username": "alice", "password": "hunter12345" }
```

**Response 200**
```json
{
  "access_token": "eyJhbGciOi…",
  "token_type": "Bearer",
  "expires_in": 1800,
  "user_id": "b4ff…d7",
  "username": "alice",
  "roles": ["VIEWER"]
}
```

### GET /api/v1/auth/me

Requires `Authorization: Bearer <jwt>`.

**Response 200**
```json
{
  "user_id": "b4ff…d7",
  "username": "alice",
  "display_name": "Alice",
  "email": "alice@example.com",
  "roles": ["VIEWER"],
  "banned": false,
  "created_at": "2026-05-16T14:00:00+00:00",
  "kyc_level": "L0"
}
```

`banned`, `created_at` and `kyc_level` were added in Phase 6g
(`feat(auth): surface kyc_level + banned + created_at on /auth/me`).
`kyc_level` defaults to `"L0"` for users who have not yet completed
a KYC review.

---

## KYC user flow (Phase 6g)

Self-service endpoints any authenticated user can call to manage
their own KYC state. Mounted under `/api/v1/me/kyc`. Admin-side
review (approve / reject / promote level) is **not yet implemented**
— see ROADMAP §5 Backlog.

All routes require `Authorization: Bearer <jwt>`. Document keys are
the closed set `{dni, selfie, address, funds}`. Level targets are
the closed set `{L1, L2, L3}` and must be exactly `current_level + 1`.

| Code | When |
|------|------|
| `KYC_UNKNOWN_DOCUMENT_KEY` | `key` outside the allowed set |
| `KYC_INVALID_DOCUMENT_DATA` | empty / missing base64 `data` |
| `KYC_INVALID_REVIEW_TARGET` | `target` outside `{L1, L2, L3}` |
| `KYC_LEVEL_SKIP_NOT_ALLOWED` | `target` is not `current_level + 1` |
| `KYC_MISSING_DOCUMENTS` | required documents for `target` not all uploaded |
| `KYC_REVIEW_IN_PROGRESS` | a review is already pending for this user |

### GET /api/v1/me/kyc/status

**Response 200**
```json
{
  "level": "L0",
  "documents": [
    { "key": "dni",     "status": "missing" },
    { "key": "selfie",  "status": "missing" },
    { "key": "address", "status": "missing" },
    { "key": "funds",   "status": "missing" }
  ]
}
```

When a review has been submitted the response also includes
`pending_review: "L1"` (the requested target) and `submitted_at`
(ISO 8601 UTC). Documents that were part of the submission carry
`status: "pending_review"`.

### POST /api/v1/me/kyc/documents

**Request**
```json
{
  "key": "dni",
  "filename": "dni.png",
  "content_type": "image/png",
  "data": "<base64 payload>"
}
```

**Response 201** — sanitised `KycDocumentRecord` (the raw `data`
payload is **never** returned by the API):
```json
{
  "key": "dni",
  "status": "uploaded",
  "uploaded_at": "2026-05-16T14:05:00+00:00",
  "filename": "dni.png",
  "content_type": "image/png"
}
```

Emits audit `KYC_DOCUMENT_UPLOADED` with
`{ "key": <key>, "content_type": <ct>, "filename": <fn> }`.

### POST /api/v1/me/kyc/review

**Request**
```json
{ "target": "L1" }
```

Required document set per target:
- `L1` → `dni`, `selfie`
- `L2` → `dni`, `selfie`, `address`
- `L3` → `dni`, `selfie`, `address`, `funds`

**Response 200** — the refreshed `KycStatusResponse` with
`pending_review` set to the target and matching `uploaded` documents
flipped to `pending_review`.

Emits audit `KYC_REVIEW_REQUESTED` with `{ "target": "L1" }`.

---

## Admin endpoints (Phase I.2–I.5, v0.14.0)

All admin routes require an authenticated user holding the right
permission. Failures return:

| HTTP | Code | When |
|------|------|------|
| 401 | `AUTH_REQUIRED` | No bearer token on the request |
| 403 | `FORBIDDEN` | Token is valid but the user lacks the gating permission |
| 400 | `USER_NOT_FOUND` | The path `<id>` does not exist |
| 400 | `USER_ALREADY_DELETED` | The user is already soft-deleted |
| 400 | `USER_NOT_DELETED` | The user is not deleted (restore attempted) |
| 400 | `WALLET_NOT_FOUND` | The wallet ID does not exist |
| 400 | `SELF_ACTION_FORBIDDEN` | An admin attempts to ban / demote themselves |
| 400 | `VALIDATION_ERROR` | Body shape, unknown role, unknown permission, etc. |
| 400 | `EMAIL_TAKEN` | Email already used by another account |

ADMIN's hardcoded baseline (in `domain/permissions.py`) covers the
user-management cluster (`CREATE_USER`, `VIEW_USERS`, `UPDATE_USER`,
`BAN_USER`, `UNBAN_USER`, `DELETE_USER`, `RESTORE_USER`, `ASSIGN_ROLE`,
`MANAGE_PERMISSIONS`, `VIEW_AUDIT_LOG`) plus wallet oversight
(`VIEW_WALLETS`, `FREEZE_WALLET`, `UNFREEZE_WALLET`) and their own wallet
ops (`CREATE_WALLET`, `TRANSFER`). `MINT` and `VIEW_TRANSFERS` remain
**outside** the baseline — even an ADMIN must self-grant them through
`POST /admin/users/<self>/permissions` (the grant is audited).

### GET /api/v1/admin/users  *(permission VIEW_USERS)*

```json
{
  "users": [
    { "user_id": "…", "username": "alice", "display_name": "Alice",
      "email": "alice@…", "banned": false, "roles": ["ADMIN"] }
  ],
  "count": 1
}
```

### PATCH /api/v1/admin/users/<id>  *(permission UPDATE_USER)*

Updates profile fields. Body is partial; only provided fields are updated.

```json
{ "display_name": "Alice Cooper", "email": "alice@new.example" }
```

**Response 200**
```json
{ "user_id": "…", "display_name": "Alice Cooper", "email": "alice@new.example" }
```

### DELETE /api/v1/admin/users/<id>  *(permission DELETE_USER)*

Soft-deletes a user and freezes all their wallets.

**Response 200**
```json
{ "user_id": "…", "deleted": true, "frozen_wallets": ["w_..."] }
```

### POST /api/v1/admin/users/<id>/restore  *(permission RESTORE_USER)*

Restores a soft-deleted user. Optional `unfreeze_wallets` (default true).

```json
{ "unfreeze_wallets": true }
```

**Response 200**
```json
{ "user_id": "…", "restored": true, "unfrozen_wallets": ["w_..."] }
```

### POST /api/v1/admin/users/<id>/roles  *(permission ASSIGN_ROLE)*

```json
{ "action": "grant", "role": "OPERATOR" }    // or "revoke"
```

Response: `{ "user_id", "roles": ["…"], "action": "ROLE_GRANTED" }`.

### POST /api/v1/admin/users/<id>/ban  *(permission BAN_USER)*

Body: empty. Self-ban is rejected with `SELF_ACTION_FORBIDDEN`.

A banned user cannot log in — login returns the uniform
`AUTH_INVALID_CREDENTIALS` (no enumeration leak). Existing JWTs
remain valid until they expire; revoke-on-ban is out of scope
for the current release.

### POST /api/v1/admin/users/<id>/unban  *(permission UNBAN_USER)*

Body: empty. Returns `{ "user_id", "banned": false }`.

### POST /api/v1/admin/users/<id>/permissions  *(permission MANAGE_PERMISSIONS)*

```json
{ "action": "grant", "permission": "VIEW_WALLETS" }
```

Response lists the user's full set of direct grants:
`{ "user_id", "permissions": ["VIEW_WALLETS"], "action": "PERMISSION_GRANTED" }`.

### GET /api/v1/admin/audit  *(permission VIEW_AUDIT_LOG)*

Returns the most recent admin audit entries (newest first). Optional
`?limit=N` (default 50, capped at 200).

```json
{
  "entries": [
    { "id": 12, "actor_id": "…", "action": "USER_BANNED",
      "target_id": "…", "details": {}, "created_at": "2026-05-07 …" }
  ],
  "count": 1
}
```

### GET /api/v1/admin/wallets  *(permission VIEW_WALLETS)*

Returns all wallets across users with owner metadata.

```json
{
  "wallets": [
    { "wallet_id": "w_...", "user_id": "...", "username": "alice",
      "display_name": "Alice", "currency": "NATIVE", "balance": "100.0",
      "public_key": "02f3...", "frozen": false }
  ],
  "count": 1
}
```

### POST /api/v1/admin/wallets/<wallet_id>/freeze  *(permission FREEZE_WALLET)*

**Response 200**
```json
{ "wallet_id": "w_...", "frozen": true }
```

### POST /api/v1/admin/wallets/<wallet_id>/unfreeze  *(permission UNFREEZE_WALLET)*

**Response 200**
```json
{ "wallet_id": "w_...", "frozen": false }
```

---

## Wallet endpoints (Phase I.3, v0.13.0)

Each wallet owns a secp256k1 keypair derived from a 12-word BIP-39
mnemonic. The server persists only `wallets.public_key`. Transfers
must be signed locally with the mnemonic-derived private key.

The canonical signing message (UTF-8 bytes):

```
f"{sender_wallet_id}:{receiver_wallet_id}:{amount}:{nonce}"
```

`amount` is the plain decimal string (no exponent), `nonce` is a
positive integer strictly greater than the wallet's last accepted
nonce. The reference Python helper is
`domain.crypto.canonical_transfer_message(...)`.

Common error codes for wallet endpoints:

| HTTP | Code | When |
|------|------|------|
| 400 | `WALLET_NOT_FOUND` | Unknown wallet ID |
| 400 | `WALLET_FROZEN` | Wallet is frozen |
| 400 | `INSUFFICIENT_BALANCE` | Sender lacks funds |
| 400 | `SIGNATURE_INVALID` | Signature fails verification |
| 400 | `NONCE_REPLAY` | Nonce is not strictly greater |
| 400 | `WALLET_OWNERSHIP` | Caller does not own sender wallet |

### POST /api/v1/wallets  *(permission CREATE_WALLET)*

Body: `{}` (currency defaults to `NATIVE`; multi-currency lands in
Phase J).

**Response 201**
```json
{
  "wallet_id": "w_a3b41c…",
  "public_key": "02f3…ab",
  "mnemonic": "twelve words you must record now this is the only time the server returns it",
  "warning": "This mnemonic is shown only once. Store it securely. It is the only way to authorise transfers from this wallet."
}
```

The mnemonic is **not** persisted on the server. Lose it and the
wallet is unrecoverable (the server can still freeze it via ADMIN, but
no signed transfer will ever be possible from that wallet again).

### GET /api/v1/wallets/me

Lists the current user's wallets (no mnemonic, no private material).

```json
{
  "wallets": [
    {
      "wallet_id": "w_a3b41c…",
      "user_id": "…",
      "currency": "NATIVE",
      "balance": 70.0,
      "public_key": "02f3…ab",
      "frozen": false
    }
  ],
  "count": 1
}
```

### POST /api/v1/transactions/signed  *(permission TRANSFER)*

```json
{
  "sender_wallet_id":   "w_a3b41c…",
  "receiver_wallet_id": "w_77ee92…",
  "amount":             30,
  "nonce":              1,
  "signature":          "abcdef0123…"
}
```

**Response 201**
```json
{
  "message": "Transaction admitted",
  "transaction": { "sender": "alice", "receiver": "bob", "amount": 30.0,
                    "sender_wallet_id": "w_a3b41c…", "receiver_wallet_id": "w_77ee92…",
                    "nonce": 1, "signature": "abcdef…" }
}
```

The transfer is added to the mempool. Balances move when the next
block is mined. Error codes:

| HTTP | Code | When |
|------|------|------|
| 400 | `VALIDATION_ERROR` | Body shape, missing fields, zero/negative amount, sender == receiver, etc. |
| 400 | `WALLET_NOT_FOUND` | One of the wallet IDs does not exist |
| 400 | `WALLET_OWNERSHIP` | Caller does not own the sender wallet |
| 400 | `WALLET_FROZEN` | Either wallet is frozen |
| 400 | `INSUFFICIENT_BALANCE` | Sender does not have enough |
| 400 | `SIGNATURE_INVALID` | Signature does not verify against the sender wallet's public key |
| 400 | `NONCE_REPLAY` | Nonce is not strictly greater than the wallet's last used nonce |

### POST /api/v1/admin/mint  *(permission MINT — NOT in ADMIN baseline)*

Coinbase credit. ADMIN must self-grant `MINT` via
`POST /admin/users/<self>/permissions` before this route works. The
mint lands in the mempool as a transaction with `signature == "MINT"`
and credits the receiver wallet at the next mine.

```json
{ "wallet_id": "w_a3b41c…", "amount": 100 }
```

Response: `{"message": "Mint queued", "transaction": {...}}`.

---

## Endpoints

### GET /api/v1/

Returns node status and route index.

**Response 200**
```json
{
  "node": "Blockchain Simulator",
  "version": "v1",
  "routes": {
    "mine":         "/api/v1/mine_block",
    "chain":        "/api/v1/chain",
    "valid":        "/api/v1/valid",
    "transactions": "/api/v1/transactions",
    "pending":      "/api/v1/transactions/pending",
    "health":       "/api/v1/health",
    "metrics":      "/api/v1/metrics",
    "nodes":        "/api/v1/nodes",
    "register":     "/api/v1/nodes/register",
    "resolve":      "/api/v1/nodes/resolve",
    "ws":           "/api/v1/ws"
  }
}
```

---

### POST /api/v1/mine_block

Mines a new block using Proof-of-Work. Flushes the mempool into the block.

**Rate limit:** 5 requests per 60 seconds (sliding window, per process).

**Request body:** none

**Response 200**
```json
{
  "message":       "A block is MINED",
  "index":         3,
  "timestamp":     "2026-04-23T21:12:44.123456",
  "proof":         84530,
  "previous_hash": "00000a3f...",
  "merkle_root":   "abcdef...",
  "transactions": [
    { "sender": "alice", "receiver": "bob", "amount": 10.5 }
  ]
}
```

The `transactions` field is also available **nested under each block** in
`GET /api/v1/chain` responses (since v0.10.0). Keeping it at the top level
of the mining response preserves back-compatibility with v0.9.0 clients.

**Response 429**
```json
{
  "error": "Too many requests. Limit: 5 per 60 seconds.",
  "code":  "RATE_LIMITED",
  "retry_after_seconds": 42
}
```
Header: `Retry-After: 42`

**Side effects:**
- WebSocket push: `{"event": "block_mined", "block": {...}}` to all subscribers.
- Concurrent `GET /api/v1/nodes/resolve` triggered on all peers (fire-and-forget).

---

### GET /api/v1/chain

Returns the full blockchain.

**Response 200**
```json
{
  "chain": [
    {
      "index":         1,
      "timestamp":     "2026-04-23T20:00:00.000000",
      "proof":         1,
      "previous_hash": "0",
      "merkle_root":   "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "transactions":  []
    },
    {
      "index":         2,
      "timestamp":     "2026-04-23T20:01:10.123456",
      "proof":         84530,
      "previous_hash": "00000a3f...",
      "merkle_root":   "abcdef...",
      "transactions": [
        { "sender": "alice", "receiver": "bob", "amount": 10.5 }
      ]
    }
  ],
  "length": 2
}
```

Since v0.10.0 each block carries `merkle_root` plus its `transactions` list.
Empty blocks use `merkle_root = sha256("").hexdigest()` (the constant
`EMPTY_MERKLE_ROOT` exported from `domain.blockchain`).

---

### GET /api/v1/valid

Validates the chain's proof-of-work linkage, hash chain integrity, **and**
(since v0.10.0) the Merkle root stamped on each block versus the
transactions actually present in the `transactions` table. Mutating a
confirmed transaction's amount, sender, or receiver after the fact will
flip this endpoint's `valid` to `false` until the chain is repaired.

**Response 200**
```json
{ "message": "The Blockchain is valid.", "valid": true }
```
or
```json
{ "message": "The Blockchain is not valid.", "valid": false }
```

---

### POST /api/v1/transactions

Adds a pending transaction to the local mempool and propagates it to peers.

**Request body**
```json
{
  "sender":   "alice",
  "receiver": "bob",
  "amount":   10.5
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `sender` | string | Non-empty; must differ from `receiver` |
| `receiver` | string | Non-empty; must differ from `sender` |
| `amount` | number | Positive (> 0) |

**Response 201**
```json
{
  "message": "Transaction added",
  "transaction": {
    "sender":   "alice",
    "receiver": "bob",
    "amount":   10.5
  }
}
```

**Response 400**
```json
{ "error": "amount must be positive", "code": "VALIDATION_ERROR" }
```

**Propagation header:** Include `X-Propagated: 1` to prevent re-forwarding
(used by the server itself when relaying to peers).

---

### GET /api/v1/transactions/pending

Returns all transactions currently in the mempool (non-destructive).

**Response 200**
```json
{
  "transactions": [
    { "sender": "alice", "receiver": "bob", "amount": 10.5 }
  ],
  "count": 1
}
```

---

### GET /api/v1/transactions

Returns the full history of confirmed transactions (those persisted to the
`transactions` table when their containing block was mined). The list is
ordered ascending by `block_index`, then by insertion order within each
block. Each entry includes the confirming block index and the block
timestamp so clients can reconstruct timeline views without a separate
chain query.

This endpoint is the read-side companion of the mining flow: when
`POST /api/v1/mine_block` runs, the mempool is flushed and its contents
inserted into the `transactions` table — making them queryable here for
the lifetime of the chain. Unlike `/transactions/pending`, this list
survives node restarts, page reloads, and cache clears.

**Response 200**
```json
{
  "transactions": [
    {
      "sender":          "alice",
      "receiver":        "bob",
      "amount":          10.5,
      "block_index":     2,
      "block_timestamp": "2026-05-06 12:34:56.789012"
    }
  ],
  "count": 1
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sender` | string | Sender as recorded in the original mempool entry |
| `receiver` | string | Receiver as recorded in the original mempool entry |
| `amount` | number | Confirmed amount (always positive) |
| `block_index` | integer | Index of the block that contains the transaction |
| `block_timestamp` | string | Timestamp of the confirming block (ISO-like, server-local) |

**Notes**

- In-memory mode keeps confirmed records inside `InMemoryBlockRepository` —
  they survive within the same process but are lost on restart. Use the
  PostgreSQL backend for cross-restart durability.
- Empty response (`{ "transactions": [], "count": 0 }`) is returned when no
  block has been mined yet on this node.

---

### POST /api/v1/nodes/register

Registers one or more peer node URLs.

**Request body**
```json
{ "nodes": ["http://localhost:5001", "localhost:5002"] }
```

URLs are normalised: scheme defaults to `http://`; path and query are stripped.

**Response 201**
```json
{
  "message": "Nodes registered",
  "total":   2,
  "nodes":   ["http://localhost:5001", "http://localhost:5002"]
}
```

**Response 400**
```json
{ "error": "nodes list must be non-empty", "code": "VALIDATION_ERROR" }
```

---

### GET /api/v1/nodes

Returns all registered peer nodes.

**Response 200**
```json
{
  "nodes": ["http://localhost:5001", "http://localhost:5002"],
  "total": 2
}
```

---

### GET /api/v1/nodes/resolve

Runs the longest-chain consensus algorithm against all registered peers.
Fetches each peer's `/api/v1/chain`, validates it, and replaces the local
chain if a longer valid one is found.

**Response 200 — chain replaced**
```json
{
  "message":  "Chain replaced with a longer one from a peer.",
  "replaced": true,
  "chain":    [ ... ]
}
```

**Response 200 — local chain authoritative**
```json
{
  "message":  "Local chain is authoritative.",
  "replaced": false,
  "chain":    [ ... ]
}
```

---

### GET /api/v1/health

Returns node health. HTTP 200 when healthy, HTTP 503 when DB is degraded.

**Response 200 — in-memory mode**
```json
{ "status": "ok", "db": "n/a", "chain_height": 3 }
```

**Response 200 — PostgreSQL reachable**
```json
{ "status": "ok", "db": "ok", "chain_height": 3 }
```

**Response 503 — PostgreSQL unreachable**
```json
{ "status": "degraded", "db": "error", "chain_height": 3 }
```

---

### GET /api/v1/metrics

Returns operational metrics.

**Response 200**
```json
{
  "chain_height":          3,
  "pending_transactions":  1,
  "avg_mine_time_seconds": 12.345
}
```

`avg_mine_time_seconds` is `null` when fewer than 2 blocks exist.

---

### WebSocket /api/v1/ws

Real-time event stream. Connect with any WebSocket client.

**Connection:** `ws://localhost:5000/api/v1/ws`

**Messages received (server → client)**

`block_mined` — emitted after every successful mine operation:
```json
{
  "event": "block_mined",
  "block": {
    "index":         3,
    "timestamp":     "2026-04-23T21:12:44.123456",
    "proof":         84530,
    "previous_hash": "00000a3f..."
  }
}
```

**Messages sent (client → server):** none. The endpoint is read-only.

**Connection lifecycle:**
1. Client connects → server allocates an asyncio queue.
2. Server continuously forwards queued messages.
3. Client disconnects → queue is removed; no message loss risk beyond a slow
   consumer (full queues drop messages silently).

---

## Legacy Endpoints (root path)

Maintained for backward compatibility. Not rate-limited.

| Method | Path | Equivalent |
|--------|------|-----------|
| `GET` | `/` | Metadata (legacy format) |
| `GET` | `/mine_block` | Mine a block (no rate limit) |
| `GET` | `/get_chain` | Full chain |
| `GET` | `/valid` | Chain validation |

These endpoints will be deprecated in a future release.

---

## Admin: Exchange Rates (Phase I.4, v1.5.0)

Admins with `MANAGE_EXCHANGE_RATES` permission can list, manually set, or sync
exchange rates from external providers.

### `GET /admin/exchange-rates`

List all exchange rates in the catalog.

**Query Parameters:**
- `from` (optional): Filter by source currency (exact match)
- `to` (optional): Filter by target currency (exact match)
- `limit` (optional, default 100): Max results to return

**Response (200 OK):**
```json
{
  "rates": [
    {
      "rate_id": 1,
      "from_currency": "BTC",
      "to_currency": "USDT",
      "rate": "80700.50",
      "fee_rate": "0.01",
      "source": "BINANCE",
      "updated_at": "2026-05-12T12:30:00Z"
    }
  ],
  "count": 1
}
```

### `PUT /admin/exchange-rates/<FROM>/<TO>`

Set a manual exchange rate (not synced from external sources).

**Request:**
```json
{
  "rate": 80700.50,
  "fee_rate": 0.01
}
```

**Response (201 Created):**
Same structure as GET, with status 201.

**Errors:**
- `VALIDATION_ERROR`: rate/fee_rate invalid or currencies identical
- `CURRENCY_NOT_FOUND`: source or target currency not in catalog
- `AUTH_INSUFFICIENT_PERMISSION`: user lacks `MANAGE_EXCHANGE_RATES`

### `POST /admin/exchange-rates/sync`

Sync exchange rates from an external provider (Binance, Crypto.com).

**Request:**
```json
{
  "provider": "CRYPTO_COM",
  "pairs": ["BTC/USDT", "ETH/USDT"]
}
```

**Supported Providers:**
- `BINANCE` – Binance spot API v3
  - Symbol format: no separator (e.g., `BTCUSDT`)
  - Endpoint: `https://api.binance.com/api/v3/ticker/price?symbol={PAIR}`
  
- `CRYPTO_COM` – Crypto.com v2 public API
  - Symbol format: underscore-separated (e.g., `BTC_USDT`)
  - Endpoint: `https://api.crypto.com/v2/public/get-ticker?instrument_name={PAIR}`

**Pairs Array:**
- Can be a list of strings: `["BTC/USDT", "ETH/USDT"]`
- Or a CSV string: `pairs_csv: "BTC/USDT,ETH/USDT"`
- Each pair must be two distinct active currencies in the catalog

**Response (200 OK):**
```json
{
  "rates": [
    {
      "rate_id": 2,
      "from_currency": "BTC",
      "to_currency": "USDT",
      "rate": "80700.38",
      "fee_rate": "0",
      "source": "CRYPTO_COM",
      "updated_at": "2026-05-12T12:35:00Z"
    },
    {
      "rate_id": 3,
      "from_currency": "ETH",
      "to_currency": "USDT",
      "rate": "2283.55",
      "fee_rate": "0",
      "source": "CRYPTO_COM",
      "updated_at": "2026-05-12T12:35:00Z"
    }
  ],
  "count": 2,
  "provider": "CRYPTO_COM"
}
```

**Errors:**
- `VALIDATION_ERROR`: invalid provider, pairs format, or currencies
- `CURRENCY_NOT_FOUND`: one or more currencies not in catalog
- `EXCHANGE_FEED_ERROR`: external API unavailable or returned invalid data
  - Example: `"Crypto.com response missing price for BTC_USDT"`
- `AUTH_INSUFFICIENT_PERMISSION`: user lacks `MANAGE_EXCHANGE_RATES`

**Example (Crypto.com):**
```bash
TOKEN="eyJhbGciOi..."
curl -X POST http://localhost:5000/api/v1/admin/exchange-rates/sync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "provider": "CRYPTO_COM",
    "pairs": ["BTC/USDT", "ETH/USDT"]
  }'
```

**Example (Binance):**
```bash
curl -X POST http://localhost:5000/api/v1/admin/exchange-rates/sync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "provider": "BINANCE",
    "pairs": ["BTC/USDT", "ETH/USDT"]
  }'
```

---

## Quick Reference

```bash
# Mine a block
curl -X POST http://localhost:5000/api/v1/mine_block

# Add a transaction
curl -X POST http://localhost:5000/api/v1/transactions \
  -H "Content-Type: application/json" \
  -d '{"sender":"alice","receiver":"bob","amount":10.5}'

# List pending transactions
curl http://localhost:5000/api/v1/transactions/pending

# Full chain
curl http://localhost:5000/api/v1/chain

# Validate chain
curl http://localhost:5000/api/v1/valid

# Register a peer
curl -X POST http://localhost:5000/api/v1/nodes/register \
  -H "Content-Type: application/json" \
  -d '{"nodes":["http://localhost:5001"]}'

# Run consensus
curl http://localhost:5000/api/v1/nodes/resolve

# Health
curl http://localhost:5000/api/v1/health

# Metrics
curl http://localhost:5000/api/v1/metrics

# List exchange rates
curl http://localhost:5000/api/v1/admin/exchange-rates \
  -H "Authorization: Bearer $TOKEN"

# Sync exchange rates from Crypto.com
curl -X POST http://localhost:5000/api/v1/admin/exchange-rates/sync \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"provider":"CRYPTO_COM","pairs":["BTC/USDT","ETH/USDT"]}'

# WebSocket (requires wscat or similar)
wscat -c ws://localhost:5000/api/v1/ws
```
