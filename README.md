# Blockchain Simulator

![Version](https://img.shields.io/badge/version-v0.12.0-blue)
![Python](https://img.shields.io/badge/python-3.13-blue)
![Tests](https://img.shields.io/badge/tests-149%20passed-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-81%25-green)

**Latest stable release:** v0.12.0

Backend blockchain simulator built with Python and Quart (ASGI). Exposes a versioned REST API to mine blocks, manage a mempool of pending transactions, query confirmed transaction history, validate chain integrity (Merkle root per block), synchronise across nodes, monitor node health, stream real-time block events via WebSocket, authenticate users with JWT, **and (since v0.12.0) enforce role-based access control with audit-logged admin actions** — with optional PostgreSQL persistence.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Component diagrams, layered design, deployment model, design decisions |
| [Business Rules](docs/business-rules.md) | All enforced rules (transactions, blocks, consensus, persistence, API) |
| [Data Model](docs/data-model.md) | ER diagram, class diagram, DDL schema |
| [Flow Diagrams](docs/flows.md) | Mermaid diagrams for every major operation |
| [Use Cases](docs/use-cases.md) | UC catalog with actors, flows, pre/postconditions |
| [API Reference](docs/api-reference.md) | Complete endpoint specification with examples |
| [Postman Collection](postman/) | Importable collection + environment for all endpoints |

---

## Architecture

```
basic-blockchain.py       ← Quart app factory (create_app)
├── api/
│   ├── errors.py         ← Uniform JSON error envelopes
│   ├── health.py         ← DB connectivity check helper
│   ├── logging_config.py ← Structured JSON logging + request-id
│   ├── rate_limit.py     ← Sliding-window rate limiter
│   ├── schemas.py        ← Request parsing and validation
│   └── websocket_hub.py  ← WebSocketHub (asyncio queues, broadcast)
├── domain/
│   ├── blockchain.py     ← BlockchainService (PoW, chain validation)
│   ├── consensus.py      ← ConsensusService (longest-chain resolve)
│   ├── mempool.py        ← MempoolService (pending transactions)
│   ├── mempool_repository.py ← MempoolRepositoryProtocol + InMemory impl
│   ├── models.py         ← Block, Transaction dataclasses
│   ├── node_registry.py  ← NodeRegistryProtocol + InMemoryNodeRegistry
│   ├── propagation.py    ← PropagationService (tx broadcast + block push)
│   ├── repository.py     ← BlockRepositoryProtocol
│   └── validation.py     ← Transaction validation rules
├── infrastructure/
│   ├── postgres_repository.py         ← PostgreSQL block storage
│   ├── postgres_mempool_repository.py ← PostgreSQL mempool storage
│   └── postgres_node_registry.py      ← PostgreSQL peer node storage
├── migrations/
│   └── versions/         ← V001–V005 idempotent SQL migrations
└── config.py             ← DATABASE_URL, DIFFICULTY_PREFIX from env
```

**Persistence modes:**
- **In-memory** (default) — zero config, used by unit tests; `python basic-blockchain.py` starts here automatically when no `.env` is present
- **PostgreSQL** — place credentials in `.env`; blocks, mempool, and peer registry survive restarts

---

## Requirements

- Python 3.10+
- PostgreSQL 14+ (optional, for persistence)

```bash
pip install -r requirements-dev.txt
```

---

## Running the server

**In-memory (no database):**
```bash
python basic-blockchain.py
```

**With PostgreSQL persistence:**
```bash
# 1. Copy and fill in credentials
cp .env.example .env

# 2. Apply migrations
export $(grep -v '^#' .env | xargs)
python migrations/migrate.py

# 3. Start
DATABASE_URL=postgresql://user:pass@localhost:5432/blockchain_simulator \
  python basic-blockchain.py
```

Server listens on `http://127.0.0.1:5000`.

---

## API — v1

Base path: `/api/v1`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Node status and route index |
| `POST` | `/mine_block` | Mine a new block (rate-limited: 5 req/60 s) |
| `GET` | `/chain` | Full chain as JSON |
| `GET` | `/valid` | Chain integrity check |
| `POST` | `/transactions` | Add a pending transaction to the mempool |
| `GET` | `/transactions/pending` | List pending transactions |
| `GET` | `/transactions` | List confirmed transactions (full history) |
| `POST` | `/auth/register` | Register a new user; returns one-shot activation code |
| `POST` | `/auth/activate` | Exchange activation code + chosen password for an active account |
| `POST` | `/auth/login` | Issue a Bearer JWT (default TTL 30 min) |
| `GET` | `/auth/me` | Current identity (requires `Authorization: Bearer <jwt>`) |
| `GET` | `/admin/users` | List users (ADMIN; permission `VIEW_USERS`) |
| `POST` | `/admin/users/<id>/roles` | Grant or revoke a role (ADMIN; permission `ASSIGN_ROLE`) |
| `POST` | `/admin/users/<id>/ban` / `/unban` | Ban / unban a user (ADMIN; permissions `BAN_USER`/`UNBAN_USER`) |
| `POST` | `/admin/users/<id>/permissions` | Grant or revoke a per-user permission override (ADMIN; permission `MANAGE_PERMISSIONS`) |
| `GET` | `/admin/audit` | Recent admin audit entries (ADMIN; permission `VIEW_AUDIT_LOG`) |
| `GET` | `/health` | Node health: DB connectivity + chain height |
| `GET` | `/metrics` | Chain height, pending tx count, avg mine time |
| `POST` | `/nodes/register` | Register one or more peer node URLs |
| `GET` | `/nodes` | List all registered peer nodes |
| `GET` | `/nodes/resolve` | Run longest-chain consensus against all peers |
| `WebSocket` | `/ws` | Real-time `block_mined` event stream |

### Quick examples

```bash
# Mine a block
curl -X POST http://127.0.0.1:5000/api/v1/mine_block

# Add a transaction
curl -X POST http://127.0.0.1:5000/api/v1/transactions \
  -H "Content-Type: application/json" \
  -d '{"sender": "alice", "receiver": "bob", "amount": 10.5}'

# Health check
curl http://127.0.0.1:5000/api/v1/health
# {"status": "ok", "db": "ok", "chain_height": 3}

# Metrics
curl http://127.0.0.1:5000/api/v1/metrics
# {"chain_height": 3, "pending_transactions": 1, "avg_mine_time_seconds": 0.412}
```

### Error envelope

All errors return `{"error": "<message>", "code": "<CODE>"}` with the appropriate HTTP status.

---

## Running tests

```bash
# Unit tests (no database required)
PYTHONPATH=. py -m pytest -q -m "not integration"

# Integration tests (requires DATABASE_URL pointing to a test DB)
PYTHONPATH=. py -m pytest -q -m integration
```

Coverage gate: **80%** (enforced in CI).

---

## Key concepts

- **Genesis block** — Created automatically on first init; not re-created on restart when using PostgreSQL.
- **Proof of Work** — SHA-256 hash of `(proof² - prev_proof²)` must start with `DIFFICULTY_PREFIX` (default `00000`).
- **Merkle root** — Each block carries a `merkle_root` over its transactions (binary sha256 tree, Bitcoin-style odd-level duplication). The chain hash covers `merkle_root`, so any post-hoc edit to a confirmed transaction makes `is_chain_valid()` return `False`. Empty blocks use `EMPTY_MERKLE_ROOT = sha256("").hexdigest()`.
- **Identity & roles** *(Phase I.1, v0.11.0)* — Three roles (`ADMIN`, `OPERATOR`, `VIEWER`); new users default to `VIEWER`. The first registered user whose username matches `BOOTSTRAP_ADMIN_USERNAME` is auto-promoted to `ADMIN`. JWTs are HS256 with `{sub, roles, iat, exp}` and a 30-min TTL. `JWT_SECRET` must be set in production (`TESTING=true` lets the test suite use a built-in sentinel). bcrypt cost is configurable through `BCRYPT_ROUNDS` (default 12).
- **RBAC with least-privilege** *(Phase I.2, v0.12.0)* — Every role-gated route uses `@require_permission(Permission.X)`. The 3-level resolver checks (1) per-user grants in `user_permissions`, (2) per-role overrides in `role_permissions`, (3) the hardcoded baselines in `domain/permissions.py`. ADMIN's baseline is **user/role/permission management only** — `MINT`, `FREEZE_WALLET`, `VIEW_WALLETS`, `VIEW_TRANSFERS` and other financial-action permissions require an explicit grant per admin (audited). OPERATOR is "audit-light" (own wallet ops + cross-user read of wallets/transfers); VIEWER operates only their own wallet. Every state-mutating admin call writes a row to `audit_log`.
- **Repository pattern** — `BlockRepositoryProtocol` and `MempoolRepositoryProtocol` decouple domain logic from storage; swap in-memory ↔ PostgreSQL without touching service code.
- **Structured logging** — Every event emits JSON `{ts, level, event, request_id, data}`; `request_id` is taken from the `X-Request-ID` header or auto-generated per request.
- **WebSocket push** — Connected clients receive `{"event": "block_mined", "block": {...}}` the moment a block is mined, without polling. Connect to `ws://localhost:5000/api/v1/ws`.
- **Async ASGI** — All route handlers are `async def` (Quart). For production, run with `hypercorn basic-blockchain:app`.
