# Postman — Blockchain Simulator

## Files

| File | Purpose |
|------|---------|
| `blockchain-simulator.postman_collection.json` | All API requests, organised by responsibility |
| `blockchain-simulator.postman_environment.json` | Environment variables for local development |

## Import

1. Open Postman.
2. **File → Import** (or drag-and-drop both JSON files).
3. Select **Blockchain Simulator — Local** in the environment picker (top-right).
4. Start the server: `python basic-blockchain.py`
5. Send **🔌 Node Status / API Index** to verify connectivity.

## Collection structure

| Folder | Requests | Responsibility |
|--------|----------|----------------|
| 🔌 Node Status | 1 | Connectivity check, route index |
| ⛏ Mining | 1 | Block mining via Proof-of-Work |
| 📋 Mempool | 4 | Submit transactions, inspect pending queue |
| 🔗 Chain | 2 | Read chain, validate integrity |
| 🌐 Peer Network | 4 | Register peers, list nodes, run consensus |
| 📊 Observability | 2 | Health check, operational metrics |
| 🧪 Error Cases | 3 | 404, 405, 400 validation errors |

## Environment variables

| Variable | Default | Set by |
|----------|---------|--------|
| `base_url` | `http://127.0.0.1:5000` | User |
| `api_base` | `{{base_url}}/api/v1` | Derived |
| `peer_url` | `http://127.0.0.1:5001` | User |
| `sender` | `alice` | User |
| `receiver` | `bob` | User |
| `amount` | `10.5` | User |
| `last_block_index` | _(empty)_ | Mine Block test script |
| `last_proof` | _(empty)_ | Mine Block test script |
| `chain_length` | _(empty)_ | Get Chain / Resolve Consensus test script |
| `pending_tx_count` | _(empty)_ | Get Pending Transactions test script |
| `chain_is_valid` | _(empty)_ | Validate Chain test script |
| `consensus_replaced` | _(empty)_ | Resolve Consensus test script |

## Automated tests

Every request includes a **Tests** tab with assertions. Run the full collection via **Run collection** (Collection Runner) to validate the API end-to-end.

Each request also injects `X-Request-ID: <uuid>` automatically (collection-level pre-request script) for log correlation.

## Multi-node setup

To test consensus and propagation:

1. Start a second node on port 5001:
   ```bash
   PORT=5001 python basic-blockchain.py
   ```
   *(or export `QUART_RUN_PORT=5001`)*

2. Register it as a peer on node 1:
   - Use **🌐 Peer Network / Register Peer Nodes** (env `peer_url` defaults to `http://127.0.0.1:5001`).

3. Mine a block on node 2, then call **Resolve Consensus** on node 1 to see it adopt the longer chain.
