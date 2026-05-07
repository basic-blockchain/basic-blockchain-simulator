# Data Model — Blockchain Simulator

## 1. Domain Model (Python)

### Block

```python
@dataclass
class Block:
    index:         int               # 1-based chain position; auto-incremented
    timestamp:     str               # ISO-8601 datetime (UTC)
    proof:         int               # Proof-of-Work result satisfying difficulty
    previous_hash: str               # SHA-256 hash of the preceding block (hex)
                                     # Genesis uses "0" as sentinel
    merkle_root:   str               # SHA-256 Merkle root over `transactions`
                                     # Empty list -> sha256("").hexdigest()
    transactions:  list[Transaction] # Confirmed in this block; hydrated at
                                     # read time from the `transactions` table
```

`merkle_root` and `transactions` were added in v0.10.0 (Phase H+). The
chain hash covers `merkle_root`, so any post-hoc edit to a confirmed
transaction makes `is_chain_valid()` return `False`.

### Transaction

```python
@dataclass
class Transaction:
    sender:   str    # Non-empty account identifier
    receiver: str    # Non-empty account identifier; must differ from sender
    amount:   float  # Positive numeric value; stored as NUMERIC(20,8) in DB
```

---

## 2. Entity-Relationship Diagram (PostgreSQL)

```mermaid
erDiagram
    blocks {
        serial      id              PK
        integer     index           UK  "domain key; 1-based"
        text        timestamp
        integer     proof
        text        previous_hash
        text        merkle_root         "NOT NULL — sha256 Merkle root over the block's transactions; introduced in V006 (Phase H+)"
        timestamptz created_at
    }

    transactions {
        serial          id          PK
        integer         block_index FK  "→ blocks.index ON DELETE CASCADE"
        text            sender
        text            receiver
        numeric_20_8    amount          "CHECK amount > 0"
    }

    mempool {
        serial          id          PK
        text            sender
        text            receiver
        numeric_20_8    amount          "CHECK amount > 0"
        timestamptz     created_at
    }

    nodes {
        text    url     PK  "normalised scheme://host:port"
    }

    schema_migrations {
        text        version     PK  "e.g. V001"
        timestamptz applied_at
    }

    blocks ||--o{ transactions : "contains (confirmed)"
```

---

## 3. Logical Data Model

```mermaid
classDiagram
    class Block {
        +int index
        +str timestamp
        +int proof
        +str previous_hash
        +to_dict() dict
    }

    class Transaction {
        +str sender
        +str receiver
        +float amount
        +to_dict() dict
    }

    class BlockchainService {
        +str difficulty_prefix
        +BlockRepositoryProtocol repository
        +create_block(proof, previous_hash) Block
        +proof_of_work(previous_proof) int
        +hash_block(block) str
        +is_chain_valid() bool
        +is_valid_chain(blocks) bool
        +replace_chain(blocks) None
        +chain_as_dicts() list
        +avg_mine_time_seconds() float|None
        +chain_length() int
    }

    class MempoolService {
        +MempoolRepositoryProtocol repository
        +add(tx Transaction) None
        +flush() list~Transaction~
        +pending() list~Transaction~
        +count() int
    }

    class ConsensusService {
        +BlockchainService blockchain
        +NodeRegistryProtocol registry
        +int timeout
        +resolve() bool
    }

    class PropagationService {
        +NodeRegistryProtocol registry
        +int timeout
        +broadcast_transaction(tx Transaction) None
        +notify_resolve() None
    }

    class WebSocketHub {
        +Set~Queue~ _clients
        +serve(send_fn) None
        +broadcast(payload dict) None
        +connection_count int
    }

    class BlockRepositoryProtocol {
        <<interface>>
        +get_all() list~Block~
        +append(block Block) None
        +last() Block
        +count() int
        +replace_all(blocks) None
    }

    class MempoolRepositoryProtocol {
        <<interface>>
        +add(tx Transaction) None
        +flush() list~Transaction~
        +pending() list~Transaction~
        +count() int
    }

    class NodeRegistryProtocol {
        <<interface>>
        +add(url str) None
        +all() list~str~
        +count() int
    }

    class InMemoryBlockRepository {
        +list~Block~ _chain
    }

    class InMemoryMempoolRepository {
        +list~Transaction~ _pending
    }

    class InMemoryNodeRegistry {
        +set~str~ _nodes
    }

    class PostgresBlockRepository {
        +str dsn
    }

    class PostgresMempoolRepository {
        +str dsn
    }

    class PostgresNodeRegistry {
        +str dsn
    }

    BlockchainService --> BlockRepositoryProtocol : uses
    MempoolService --> MempoolRepositoryProtocol : uses
    ConsensusService --> BlockchainService : uses
    ConsensusService --> NodeRegistryProtocol : uses
    PropagationService --> NodeRegistryProtocol : uses

    BlockRepositoryProtocol <|.. InMemoryBlockRepository : implements
    BlockRepositoryProtocol <|.. PostgresBlockRepository : implements
    MempoolRepositoryProtocol <|.. InMemoryMempoolRepository : implements
    MempoolRepositoryProtocol <|.. PostgresMempoolRepository : implements
    NodeRegistryProtocol <|.. InMemoryNodeRegistry : implements
    NodeRegistryProtocol <|.. PostgresNodeRegistry : implements

    BlockchainService "1" --> "many" Block : manages
    MempoolService "1" --> "many" Transaction : queues
```

---

## 4. Database Schema (DDL)

### V001 — Migration tracking

```sql
CREATE TABLE schema_migrations (
    version    TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
```

### V002 — Block storage

```sql
CREATE TABLE blocks (
    id            SERIAL      PRIMARY KEY,
    index         INTEGER     UNIQUE NOT NULL,
    timestamp     TEXT        NOT NULL,
    proof         INTEGER     NOT NULL,
    previous_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_blocks_index ON blocks (index);
```

### V003 — Mempool (pending transactions)

```sql
CREATE TABLE mempool (
    id         SERIAL          PRIMARY KEY,
    sender     TEXT            NOT NULL,
    receiver   TEXT            NOT NULL,
    amount     NUMERIC(20, 8)  NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX idx_mempool_created_at ON mempool (created_at);
```

### V004 — Confirmed transactions

```sql
CREATE TABLE transactions (
    id          SERIAL          PRIMARY KEY,
    block_index INTEGER         NOT NULL REFERENCES blocks (index) ON DELETE CASCADE,
    sender      TEXT            NOT NULL,
    receiver    TEXT            NOT NULL,
    amount      NUMERIC(20, 8)  NOT NULL CHECK (amount > 0)
);
CREATE INDEX idx_transactions_block_index ON transactions (block_index);
CREATE INDEX idx_transactions_sender      ON transactions (sender);
CREATE INDEX idx_transactions_receiver    ON transactions (receiver);
```

### V005 — Peer node registry

```sql
CREATE TABLE nodes (
    url TEXT PRIMARY KEY
);
```

---

## 5. Persistence Mode Comparison

| Concern | In-Memory | PostgreSQL |
|---------|-----------|------------|
| Setup | None (default) | `DATABASE_URL` env var + `migrate.py` |
| Data survival on restart | Lost | Preserved |
| Genesis block | Re-created on every start | Created once; detected via `count() > 0` |
| Mempool flush atomicity | In-process list swap | Single DB transaction |
| Used in | Unit tests, local dev | Staging, production |
| Swap mechanism | Inject alternate `BlockRepositoryProtocol` implementation | Same interface, different constructor |
