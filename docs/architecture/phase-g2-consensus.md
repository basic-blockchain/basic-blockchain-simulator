# Architecture Decision: Multi-node Consensus (Phase G.2)

## Context

Phase G.2 adds the ability for blockchain nodes to register peer nodes and converge on the longest valid chain. This document records the design decisions made and their rationale.

---

## Decision 1: Longest-chain rule as consensus mechanism

**Chosen:** Nakamoto-style longest valid chain wins.

**Why:** It is the simplest correct consensus rule that does not require coordination between nodes. Any node that receives a chain longer than its own and passes full validation (hash links + PoW prefix) will adopt it. This maps directly to how Bitcoin resolves forks and is appropriate for a simulator at this stage.

**Rejected alternatives:**
- Round-robin / leader election: requires liveness guarantees and coordination that add significant complexity.
- BFT (Byzantine Fault Tolerant): overkill for a local simulator; assumes adversarial nodes.

---

## Decision 2: REST pull-model for chain synchronisation

**Chosen:** `GET /api/v1/chain` pull — the resolving node fetches the full chain from each peer on demand.

**Why:**
- Stateless and easy to test: no persistent connection to manage.
- Fits the current single-process, single-thread Flask dev server.
- Compatible with the existing `/chain` endpoint already in the API.

**Rejected alternative — WebSocket push:** Would require an async server (e.g. Flask-SocketIO or an ASGI framework) and adds connection lifecycle management. Deferred to Phase G.3.

**Known limitation:** Full chain transfer is O(n) in chain length. For long chains this is inefficient. A delta-sync protocol (transferring only blocks since the last common ancestor) is a Phase G.3 concern.

---

## Decision 3: In-memory node registry (Phase G.2), PostgreSQL deferred to G.3

**Chosen:** `InMemoryNodeRegistry` — a `set[str]` behind a `NodeRegistryProtocol`.

**Why:** Peer lists are small, volatile, and process-scoped at this stage. Adding a DB table and migration for a feature that resets on every restart adds churn before the interaction model is validated.

**Consequence:** Registered peers are lost on process restart. Operators must re-register peers after each restart. This is acceptable for local multi-node simulation.

**When to revisit:** Phase G.3 — add `PostgresNodeRegistry` implementing the same `NodeRegistryProtocol` and a `V005-nodes.sql` migration.

---

## Decision 4: URL normalisation policy

All node URLs are normalised to `scheme://host:port` on registration:
- URLs without `://` are prefixed with `http://`.
- Path, query string, and fragment are stripped.
- Duplicates (same normalised form) are silently ignored.

**Why:** Prevents the same peer being registered multiple times under `localhost:5001` vs `http://localhost:5001/` vs `http://localhost:5001/api`.

---

## Limitations in this phase

| Limitation | Impact | Phase to address |
|---|---|---|
| Full chain transfer on resolve | Slow for long chains | G.3 |
| No automatic peer discovery | Peers must be registered manually | G.3 (gossip) |
| No push notification on new block | Peers learn of new blocks only on next resolve | G.3 (WebSocket) |
| Registry lost on restart | Peers must be re-registered | G.3 (PostgresNodeRegistry) |
| No protection against chain inflation attacks | A peer could send an arbitrarily long valid-looking chain | H (PoW difficulty validation) |

---

## Component diagram

```
Node A                          Node B
──────────────────────          ──────────────────────
POST /nodes/register            (running independently)
  → InMemoryNodeRegistry
      stores "http://B:5001"

GET /nodes/resolve
  → ConsensusService.resolve()
      → _fetch_chain("http://B:5001")
          → GET http://B:5001/api/v1/chain
      → is_valid_chain(remote_blocks)  ← BlockchainService._validate_blocks()
      → if len(remote) > local:
            replace_chain(remote_blocks)
                → InMemoryBlockRepository.replace_all()
```
