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

# WebSocket (requires wscat or similar)
wscat -c ws://localhost:5000/api/v1/ws
```
