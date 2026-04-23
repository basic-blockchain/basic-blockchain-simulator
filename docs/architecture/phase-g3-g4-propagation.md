# Architecture Decision: Transaction Propagation and Block Notification (Phase G.3/G.4)

## Context

Phase G.2 gave nodes the ability to register peers and converge on the longest valid chain via an on-demand `GET /nodes/resolve`. However, two gaps remained:

1. **Transaction propagation** — a transaction added to one node was invisible to peers until a full resolve cycle ran.
2. **Block notification** — after mining, peers learned about the new block only if they happened to call resolve themselves.
3. **Peer persistence** — the `InMemoryNodeRegistry` lost all registered peers on process restart.

This document records the design decisions made in G.3/G.4 to close these gaps.

---

## Decision 1: PropagationService as a dedicated domain service

**Chosen:** new `PropagationService` in `domain/propagation.py` with two responsibilities:
- `broadcast_transaction(tx)` — fire `POST /api/v1/transactions` to every registered peer.
- `notify_resolve()` — fire `GET /api/v1/nodes/resolve` on every registered peer.

**Why:** keeping propagation logic in the domain layer (not in route handlers) respects the existing layering — route handlers stay thin, the service is independently testable, and a future async implementation can be swapped in without touching Flask code.

**Rejected alternative — inline in route handler:** would couple propagation to Flask's request context and make unit testing require a full app instance.

---

## Decision 2: Synchronous fire-and-forget with silent error suppression

**Chosen:** each peer call is made synchronously inside a `try/except Exception: pass` block. Peer failures are swallowed silently.

**Why:** for a simulator, a slow or unreachable peer must not block the caller's response. The synchronous approach avoids introducing threading or async primitives at this stage. The longest-chain consensus already provides eventual consistency — a peer that missed a broadcast will converge on the next `resolve` call.

**Limitations:**
- A node with many slow peers will have increased mine/add-transaction latency.
- No retry logic; missed propagations are not replayed.

**Deferred:** background thread pool or async task queue (e.g. `concurrent.futures.ThreadPoolExecutor`) for non-blocking dispatch — deferred to a future phase.

---

## Decision 3: X-Propagated header as loop-breaker

**Chosen:** when broadcasting a transaction, `PropagationService._post()` adds `X-Propagated: 1` to the request. The `POST /transactions` handler checks for this header and skips re-broadcasting if present.

**Why:** without a loop-breaker, node A broadcasts to node B, which broadcasts back to node A, causing infinite relay loops. The header is the simplest stateless solution — no shared state or message IDs required.

**Rejected alternative — track seen transaction IDs:** requires shared state across requests and a TTL eviction policy. Adds complexity without meaningful gain for a two-node scenario.

---

## Decision 4: PostgresNodeRegistry for durable peer storage

**Chosen:** `PostgresNodeRegistry` in `infrastructure/postgres_node_registry.py` implements `NodeRegistryProtocol` against a `nodes (url TEXT PRIMARY KEY)` table. `create_app()` selects it automatically when `dsn` is set; `InMemoryNodeRegistry` is used otherwise.

**Why:** registered peers should survive process restarts. The existing repository pattern makes the swap transparent — no domain code changes. `ON CONFLICT DO NOTHING` preserves the deduplication guarantee from the in-memory implementation.

**Migration:** V005 creates the `nodes` table following the same idempotent pattern as prior migrations.

---

## Component interactions after G.3/G.4

```
POST /transactions
  └─ MempoolService.add(tx)
  └─ PropagationService.broadcast_transaction(tx)  ──► peer POST /transactions (X-Propagated: 1)

POST /mine_block
  └─ BlockchainService.create_block(...)
  └─ MempoolService.flush()
  └─ PropagationService.notify_resolve()            ──► peer GET /nodes/resolve
                                                           └─ ConsensusService.resolve()
                                                                └─ fetches GET /chain from this node
```

---

## Known limitations

| Limitation | Impact | Future mitigation |
|---|---|---|
| Synchronous peer calls | Mine latency scales with peer count | ThreadPoolExecutor / async |
| No retry on broadcast failure | Missed transactions require manual resolve | Retry queue (G.5+) |
| No authentication on peer endpoints | Any node can register and receive broadcasts | mTLS / API keys (G.5+) |
| WebSocket push not implemented | Peers learn about blocks via polling | Flask-SocketIO (future phase) |
