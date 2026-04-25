# Flow Diagrams — Blockchain Simulator

All diagrams use [Mermaid](https://mermaid.js.org/) syntax.

---

## 1. Application Startup Flow

```mermaid
flowchart TD
    A([Start: python basic-blockchain.py]) --> B{DATABASE_URL set?}
    B -- No --> C[Load InMemoryBlockRepository\nInMemoryMempoolRepository\nInMemoryNodeRegistry]
    B -- Yes --> D[Load PostgresBlockRepository\nPostgresMempoolRepository\nPostgresNodeRegistry]
    C --> E[BlockchainService init]
    D --> E
    E --> F{Repository empty?}
    F -- Yes --> G[Create Genesis Block\nindex=1, proof=1, prev_hash=0]
    F -- No --> H[Attach to existing chain]
    G --> I[MempoolService init]
    H --> I
    I --> J[PropagationService init]
    J --> K[ConsensusService init]
    K --> L[WebSocketHub init]
    L --> M[Register API blueprints\n+ error handlers]
    M --> N([Server listening on :5000])
```

---

## 2. Mining Flow (POST /api/v1/mine_block)

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API Handler
    participant RL as RateLimiter
    participant BS as BlockchainService
    participant MS as MempoolService
    participant WS as WebSocketHub
    participant PS as PropagationService
    participant P as Peer Nodes

    C->>API: POST /api/v1/mine_block
    API->>RL: check(max=5, window=60s)
    alt limit exceeded
        RL-->>C: 429 RATE_LIMITED + Retry-After
    end
    RL-->>API: allowed
    API->>BS: previous_block()
    BS-->>API: prev_block
    API->>BS: proof_of_work(prev_proof)
    Note over BS: Brute-force:<br/>SHA256(p²−pp²).startswith(prefix)
    BS-->>API: new_proof
    API->>BS: hash_block(prev_block)
    BS-->>API: prev_hash
    API->>BS: create_block(new_proof, prev_hash)
    BS-->>API: new_block (persisted)
    API->>MS: flush()
    MS-->>API: [Transaction, ...]
    API->>WS: broadcast({event: block_mined, block: {...}})
    WS-->>C: WebSocket push (all subscribers)
    API->>PS: notify_resolve()
    PS--)P: GET /api/v1/nodes/resolve (concurrent, fire-and-forget)
    API-->>C: 200 {index, timestamp, proof, transactions}
```

---

## 3. Transaction Submission Flow (POST /api/v1/transactions)

```mermaid
flowchart TD
    A([Client: POST /transactions]) --> B[parse_transaction\nschema validation]
    B --> C{Fields valid?}
    C -- No --> D[400 VALIDATION_ERROR]
    C -- Yes --> E[validate_transaction\nbusiness rules]
    E --> F{Rules pass?\nBR-TX-01 to BR-TX-04}
    F -- No --> G[400 VALIDATION_ERROR]
    F -- Yes --> H[MempoolService.add]
    H --> I{X-Propagated\nheader present?}
    I -- Yes --> J[201 Transaction added\nno re-broadcast]
    I -- No --> K[PropagationService\n.broadcast_transaction]
    K --> L[ThreadPoolExecutor\nup to 8 workers]
    L --> M[POST to each peer\nwith X-Propagated:1]
    M --> J
```

---

## 4. Consensus Resolution Flow (GET /api/v1/nodes/resolve)

```mermaid
flowchart TD
    A([Client: GET /nodes/resolve]) --> B[ConsensusService.resolve]
    B --> C[Read NodeRegistry.all]
    C --> D{Any peers?}
    D -- No --> E[return replaced=false]
    D -- Yes --> F[For each peer]
    F --> G[GET peer/api/v1/chain\ntimeout=5s]
    G --> H{Request\nsucceeded?}
    H -- No --> I[Skip peer]
    I --> F
    H -- Yes --> J[Parse JSON → Block list]
    J --> K{Valid chain AND\nlonger than best?}
    K -- No --> I
    K -- Yes --> L[Update best_chain\nbest_length]
    L --> F
    F --> M{best_chain\nfound?}
    M -- No --> N[200 replaced=false\ncurrent chain]
    M -- Yes --> O[BlockchainService\n.replace_chain best_chain]
    O --> P[200 replaced=true\nnew chain]
```

---

## 5. WebSocket Event Flow

```mermaid
sequenceDiagram
    participant Sub as WS Subscriber
    participant WS as WebSocketHub
    participant API as mine_block handler

    Sub->>WS: Connect ws://host/api/v1/ws
    WS->>WS: _make_queue() → register Q in _clients
    loop While connected
        API->>WS: broadcast({event:block_mined, block:{...}})
        WS->>WS: Q.put_nowait(json_str)
        WS->>Sub: send(json_str)
    end
    Sub-->>WS: Disconnect (CancelledError)
    WS->>WS: _remove_queue(Q)
```

---

## 6. Transaction Propagation Loop Prevention

```mermaid
sequenceDiagram
    participant C as Client
    participant N1 as Node 1
    participant N2 as Node 2
    participant N3 as Node 3

    C->>N1: POST /transactions\n(no X-Propagated header)
    N1->>N1: add to mempool
    N1->>N2: POST /transactions\nX-Propagated: 1
    N1->>N3: POST /transactions\nX-Propagated: 1
    Note over N2: header present → add only, no re-broadcast
    Note over N3: header present → add only, no re-broadcast
    N2->>N2: add to mempool
    N3->>N3: add to mempool
    N1-->>C: 201 Created
```

---

## 7. Database Migration Flow (python migrations/migrate.py)

```mermaid
flowchart TD
    A([python migrations/migrate.py]) --> B[load_dotenv]
    B --> C{DATABASE_URL set?}
    C -- No --> D[ERROR: exit 1]
    C -- Yes --> E[Parse DSN]
    E --> F[Connect to postgres\nmaintenance DB]
    F --> G{Target DB\nexists?}
    G -- No --> H[CREATE DATABASE]
    G -- Yes --> I[Log: already exists]
    H --> J[Connect to target DB]
    I --> J
    J --> K[Bootstrap schema_migrations\nCREATE TABLE IF NOT EXISTS]
    K --> L[Read V*.sql from versions/]
    L --> M[SELECT applied versions]
    M --> N[Filter pending files]
    N --> O{Any pending?}
    O -- No --> P[Log: up to date\nexit 0]
    O -- Yes --> Q[For each pending file]
    Q --> R[Execute SQL in transaction]
    R --> S{Success?}
    S -- No --> T[ROLLBACK\nprint ERROR\nexit 1]
    S -- Yes --> U[COMMIT\nInsert version into schema_migrations]
    U --> Q
    Q --> V[exit 0]
```

---

## 8. Proof-of-Work Algorithm

```mermaid
flowchart TD
    A([proof_of_work called\nprev_proof]) --> B[new_proof = 1]
    B --> C{SHA256\nnew_proof² − prev_proof²\nstarts with DIFFICULTY_PREFIX?}
    C -- No --> D[new_proof += 1]
    D --> C
    C -- Yes --> E([Return new_proof])
```

---

## 9. Chain Validation Algorithm

```mermaid
flowchart TD
    A([is_chain_valid called]) --> B[blocks = repository.get_all]
    B --> C[i = 1]
    C --> D{i < len blocks?}
    D -- No --> E([return True])
    D -- Yes --> F[block = blocks i\nprev = blocks i−1]
    F --> G{block.previous_hash\n== hash prev?}
    G -- No --> H([return False])
    G -- Yes --> I{SHA256\nblock.proof² − prev.proof²\nstarts with prefix?}
    I -- No --> H
    I -- Yes --> J[i += 1]
    J --> D
```

---

## 10. Request ID Lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as before_request hook
    participant H as Route handler
    participant L as JSON Logger

    C->>MW: HTTP request (optionally with X-Request-ID header)
    MW->>MW: read X-Request-ID or generate UUID4
    MW->>MW: g.request_id = request_id
    MW->>H: dispatch
    H->>L: logger.info("event", extra={"data": {...}})
    L->>L: include g.request_id in JSON log entry
    H-->>C: HTTP response
```
