# Architecture — Blockchain Simulator

## 1. Overview

The Blockchain Simulator is a single-node, educational blockchain implementation
built in Python. It exposes a versioned REST API, supports optional PostgreSQL
persistence, propagates transactions and consensus triggers to registered peer
nodes, and pushes real-time block-mined events via WebSocket.

**Technology stack**

| Concern | Technology |
|---------|-----------|
| Language | Python 3.10+ |
| Web framework | Quart 0.19+ (ASGI, async) |
| ASGI runner (prod) | Hypercorn |
| Persistence (optional) | PostgreSQL 14+ via psycopg2-binary |
| Concurrency | asyncio (request handling) + ThreadPoolExecutor (peer HTTP calls) |
| Test runner | pytest 8+ with pytest-asyncio, pytest-cov |
| Environment config | python-dotenv |

---

## 2. High-Level Component Diagram

```mermaid
graph TB
    subgraph Client Tier
        REST[REST Client\ncurl / Postman / Peer Node]
        WSC[WebSocket Client\nbrowser / monitoring]
    end

    subgraph Application Tier
        direction TB
        API[API Layer\nQuart Blueprint /api/v1]
        DOMAIN[Domain Layer\nBlockchainService · MempoolService\nConsensusService · PropagationService]
        INFRA[Infrastructure Layer\nPostgres repositories]
        HUB[WebSocketHub\nasyncio queues]
    end

    subgraph Persistence Tier
        PG[(PostgreSQL)]
        MEM[In-Memory\nPython lists & sets]
    end

    subgraph Peer Network
        PEER1[Peer Node A]
        PEER2[Peer Node B]
    end

    REST -- HTTP/JSON --> API
    WSC -- WebSocket --> HUB
    API --> DOMAIN
    API --> HUB
    DOMAIN --> INFRA
    INFRA -- psycopg2 --> PG
    DOMAIN --> MEM
    DOMAIN -- HTTP --> PEER1
    DOMAIN -- HTTP --> PEER2
```

---

## 3. Layered Architecture

```mermaid
graph TD
    subgraph api["API Layer  (api/)"]
        routes["Routes & handlers\nbasic-blockchain.py"]
        schema["Request validation\napi/schemas.py"]
        errors["Error envelopes\napi/errors.py"]
        ratelimit["Rate limiter\napi/rate_limit.py"]
        logging["JSON logger\napi/logging_config.py"]
        hub["WebSocketHub\napi/websocket_hub.py"]
        health["DB health check\napi/health.py"]
    end

    subgraph domain["Domain Layer  (domain/)"]
        bs["BlockchainService\ndomain/blockchain.py"]
        ms["MempoolService\ndomain/mempool.py"]
        cs["ConsensusService\ndomain/consensus.py"]
        ps["PropagationService\ndomain/propagation.py"]
        nr["NodeRegistry\ndomain/node_registry.py"]
        val["Validation rules\ndomain/validation.py"]
        models["Block · Transaction\ndomain/models.py"]
        protos["Repository protocols\ndomain/repository.py\ndomain/mempool_repository.py"]
    end

    subgraph infra["Infrastructure Layer  (infrastructure/)"]
        pgb["PostgresBlockRepository"]
        pgm["PostgresMempoolRepository"]
        pgn["PostgresNodeRegistry"]
    end

    subgraph config["Configuration  (config.py)"]
        env["DATABASE_URL\nDIFFICULTY_PREFIX\nTESTING"]
    end

    routes --> schema
    routes --> bs
    routes --> ms
    routes --> cs
    routes --> ps
    routes --> hub
    routes --> health
    routes --> ratelimit
    bs --> protos
    ms --> protos
    cs --> bs
    cs --> nr
    ps --> nr
    protos --> pgb
    protos --> pgm
    nr --> pgn
    pgb --> db[(PostgreSQL)]
    pgm --> db
    pgn --> db
```

---

## 4. Deployment Diagram

```mermaid
graph LR
    subgraph node1["Node 1  (host:5000)"]
        app1["Quart app\n+ WebSocketHub"]
        db1[(PostgreSQL)]
        app1 --- db1
    end

    subgraph node2["Node 2  (host:5001)"]
        app2["Quart app\n+ WebSocketHub"]
        db2[(PostgreSQL)]
        app2 --- db2
    end

    subgraph node3["Node 3  (host:5002)"]
        app3["Quart app\n+ WebSocketHub"]
        db3[(PostgreSQL)]
        app3 --- db3
    end

    app1 -- propagate tx /\nconsensus resolve --> app2
    app1 -- propagate tx /\nconsensus resolve --> app3
    app2 -- propagate tx /\nconsensus resolve --> app1
    app2 -- propagate tx /\nconsensus resolve --> app3
    app3 -- propagate tx /\nconsensus resolve --> app1
    app3 -- propagate tx /\nconsensus resolve --> app2

    Client([Client]) -- REST / WebSocket --> app1
```

---

## 5. Module Dependency Map

```
basic-blockchain.py
├── config.py
├── api/
│   ├── errors.py          ← Quart
│   ├── health.py          ← psycopg2
│   ├── logging_config.py  ← Quart (g)
│   ├── rate_limit.py      ← Quart (jsonify)
│   ├── schemas.py         ← domain/models.py
│   └── websocket_hub.py   ← asyncio, Quart (websocket)
├── domain/
│   ├── models.py          ← stdlib only
│   ├── validation.py      ← domain/models.py
│   ├── repository.py      ← domain/models.py
│   ├── mempool_repository.py ← domain/models.py
│   ├── node_registry.py   ← stdlib only
│   ├── blockchain.py      ← domain/models.py, domain/repository.py
│   ├── mempool.py         ← domain/mempool_repository.py, domain/validation.py
│   ├── consensus.py       ← domain/blockchain.py, domain/node_registry.py
│   └── propagation.py     ← domain/node_registry.py, domain/models.py
└── infrastructure/
    ├── postgres_repository.py         ← psycopg2, domain/models.py
    ├── postgres_mempool_repository.py ← psycopg2, domain/models.py
    └── postgres_node_registry.py      ← psycopg2, domain/node_registry.py
```

---

## 6. Key Design Decisions

### Repository Pattern
All domain services depend on protocol interfaces (`BlockRepositoryProtocol`,
`MempoolRepositoryProtocol`, `NodeRegistryProtocol`). Concrete implementations
(in-memory vs PostgreSQL) are injected at startup. This makes unit tests run
without a database and allows the persistence backend to be swapped without
touching service code.

### Async-First with sync DB driver
Quart is ASGI-native and all route handlers are `async def`. psycopg2 is
synchronous and runs on the event loop thread — acceptable for a simulator.
For high-throughput production workloads, migrate to `asyncpg`.

### Fire-and-Forget Propagation
`PropagationService` dispatches HTTP calls to peers via `ThreadPoolExecutor`
(up to 8 workers). Errors are silently swallowed; there is no retry or
acknowledgement. This keeps mining latency low at the cost of eventual consistency.

### X-Propagated Loop Prevention
When a node forwards a transaction to its peers it adds `X-Propagated: 1`.
Receiving nodes store the transaction but do not re-forward it, preventing
infinite relay loops in fully-connected topologies.

### Sliding-Window Rate Limiting
The rate limiter is a process-level counter (not distributed). It is intentional
for a single-process educational simulator. In a multi-worker deployment, a
shared store (Redis) would be required.

### Injectable WebSocketHub
`WebSocketHub.serve()` accepts an optional `send_fn` parameter. In production
it defaults to `quart.websocket.send`. In tests, a `fake_send` is injected,
avoiding the need for a real WebSocket context and keeping tests fast and
deterministic.

---

## 7. Configuration Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | `str \| None` | `None` | PostgreSQL DSN. If absent, in-memory mode is used. |
| `DIFFICULTY_PREFIX` | `str` | `"00000"` | Leading zeros required in a valid block hash. Increase for harder mining. |
| `TESTING` | `bool` | `False` | Set to `1` / `true` / `yes` to enable Quart test mode. |

---

## 8. Test Architecture

```mermaid
graph LR
    subgraph Unit Tests
        domain_tests["test_blockchain_domain.py\ntest_phase_d.py\ntest_phase_f.py\ntest_phase_g2_domain.py\ntest_phase_g3_g4.py\ntest_phase_g6_ws.py"]
    end
    subgraph API Tests
        api_tests["test_blockchain_api.py\ntest_phase_g2_api.py"]
    end
    subgraph Integration Tests
        int_tests["test_integration_pg.py"]
    end

    domain_tests -- InMemory repos --> app[create_app]
    api_tests -- Quart test client --> app
    int_tests -- Real PostgreSQL --> db[(Test DB)]

    ci[CI pipeline] -- PYTHONPATH=. py -m pytest -m 'not integration' --> domain_tests
    ci --> api_tests
    ci -- requires DATABASE_URL --> int_tests
```

**Coverage gate:** 80% across `domain/`, `api/`, `infrastructure/`.

---

## 9. Security Considerations (Current Scope)

| Area | Current implementation | Production recommendation |
|------|----------------------|--------------------------|
| Authentication | None | JWT or API-key middleware |
| TLS | None (dev server) | Hypercorn with TLS cert or reverse proxy (nginx) |
| Input validation | Schema + business-rule validation | Add JSON Schema validation library |
| Rate limiting | Process-local sliding window | Distributed rate limiter (Redis + token bucket) |
| URL scheme enforcement | http/https only (propagation, consensus) | Same; add allowlist of peer IPs |
| Logging | Structured JSON | Ship to ELK / Loki; redact sensitive fields |
| DB credentials | Via `DATABASE_URL` env var / `.env` | Secrets manager (Vault, AWS Secrets Manager) |
