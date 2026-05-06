from __future__ import annotations

import uuid

from dotenv import load_dotenv

load_dotenv()

from quart import Blueprint, Quart, g, jsonify, request

from api.errors import bad_request, register_error_handlers
from api.health import check_db_connectivity
from api.logging_config import logger
from api.rate_limit import rate_limit
from api.schemas import parse_transaction
from api.websocket_hub import WebSocketHub
from config import DATABASE_URL, DIFFICULTY_PREFIX
from domain import BlockchainService, ConsensusService, InMemoryNodeRegistry, MempoolService, PropagationService
from infrastructure.postgres_mempool_repository import PostgresMempoolRepository
from infrastructure.postgres_node_registry import PostgresNodeRegistry
from infrastructure.postgres_repository import PostgresBlockRepository


def _legacy_home_payload() -> dict[str, object]:
    return {
        "message": "Blockchain simulator is running",
        "routes": {
            "mine_block": "/mine_block",
            "get_chain": "/get_chain",
            "valid": "/valid",
        },
    }


def _v1_home_payload() -> dict[str, object]:
    return {
        "message": "Blockchain simulator is running",
        "routes": {
            "mine_block": "/api/v1/mine_block",
            "chain": "/api/v1/chain",
            "valid": "/api/v1/valid",
            "transactions": "/api/v1/transactions",
            "pending": "/api/v1/transactions/pending",
            "health": "/api/v1/health",
            "metrics": "/api/v1/metrics",
            "nodes_register": "/api/v1/nodes/register",
            "nodes": "/api/v1/nodes",
            "nodes_resolve": "/api/v1/nodes/resolve",
            "ws": "/api/v1/ws",
        },
    }


def _mine(blockchain: BlockchainService, mempool: MempoolService) -> dict[str, object]:
    previous_block = blockchain.previous_block()
    previous_proof = previous_block.proof
    proof = blockchain.proof_of_work(previous_proof)
    previous_hash = blockchain.hash_block(previous_block)
    block = blockchain.create_block(proof, previous_hash)
    included = mempool.flush()
    blockchain.save_confirmed_transactions(block.index, included)
    included_dicts = [tx.to_dict() for tx in included]
    logger.info(
        "block_mined",
        extra={"data": {"index": block.index, "proof": block.proof, "tx_count": len(included_dicts)}},
    )
    return {
        "message": "A block is MINED",
        "index": block.index,
        "timestamp": block.timestamp,
        "proof": block.proof,
        "previous_hash": block.previous_hash,
        "transactions": included_dicts,
    }


def create_app(
    blockchain: BlockchainService | None = None,
    mempool: MempoolService | None = None,
    dsn: str | None = None,
    node_registry=None,
    propagation: PropagationService | None = None,
    ws_hub: WebSocketHub | None = None,
) -> Quart:
    app = Quart(__name__)
    if dsn:
        chain_service = blockchain or BlockchainService(
            repository=PostgresBlockRepository(dsn),
            difficulty_prefix=DIFFICULTY_PREFIX,
        )
        pool = mempool or MempoolService(repository=PostgresMempoolRepository(dsn))
        registry = node_registry or PostgresNodeRegistry(dsn)
    else:
        chain_service = blockchain or BlockchainService(difficulty_prefix=DIFFICULTY_PREFIX)
        pool = mempool or MempoolService()
        registry = node_registry or InMemoryNodeRegistry()

    consensus = ConsensusService(blockchain=chain_service, registry=registry)
    propagator = propagation or PropagationService(registry=registry)
    hub = ws_hub or WebSocketHub()

    @app.before_request
    async def _assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    @app.route("/", methods=["GET"])
    async def home():
        return jsonify(_legacy_home_payload()), 200

    @api_v1.route("/", methods=["GET"])
    async def v1_home():
        return jsonify(_v1_home_payload()), 200

    @api_v1.route("/mine_block", methods=["POST"])
    @rate_limit(max_calls=5, period_seconds=60)
    async def v1_mine_block():
        result = _mine(chain_service, pool)
        hub.broadcast({"event": "block_mined", "block": result})
        propagator.notify_resolve()
        return jsonify(result), 200

    @api_v1.route("/chain", methods=["GET"])
    async def v1_chain():
        chain = chain_service.chain_as_dicts()
        return jsonify({"chain": chain, "length": len(chain)}), 200

    @api_v1.route("/valid", methods=["GET"])
    async def v1_valid():
        is_valid = chain_service.is_chain_valid()
        logger.info("chain_validated", extra={"data": {"valid": is_valid}})
        message = (
            "The Blockchain is valid." if is_valid else "The Blockchain is not valid."
        )
        return jsonify({"message": message, "valid": is_valid}), 200

    @api_v1.route("/transactions", methods=["POST"])
    async def v1_add_transaction():
        data = await request.get_json(silent=True)
        try:
            tx = parse_transaction(data)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        try:
            pool.add(tx)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        logger.info(
            "tx_added",
            extra={"data": {"sender": tx.sender, "receiver": tx.receiver, "amount": tx.amount}},
        )
        if not request.headers.get("X-Propagated"):
            propagator.broadcast_transaction(tx)
        return jsonify({"message": "Transaction added", "transaction": tx.to_dict()}), 201

    @api_v1.route("/transactions/pending", methods=["GET"])
    async def v1_pending_transactions():
        pending = [tx.to_dict() for tx in pool.pending()]
        return jsonify({"transactions": pending, "count": len(pending)}), 200

    @api_v1.route("/transactions", methods=["GET"])
    async def v1_confirmed_transactions():
        confirmed = chain_service.confirmed_transactions()
        return jsonify({"transactions": confirmed, "count": len(confirmed)}), 200

    @api_v1.route("/nodes/register", methods=["POST"])
    async def v1_nodes_register():
        data = await request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return bad_request("JSON body required", "VALIDATION_ERROR")
        nodes = data.get("nodes")
        if not nodes or not isinstance(nodes, list):
            return bad_request("Field 'nodes' must be a non-empty list", "VALIDATION_ERROR")
        for url in nodes:
            if not isinstance(url, str) or not url.strip():
                return bad_request(f"Invalid node URL: {url!r}", "VALIDATION_ERROR")
            registry.add(url.strip())
        return jsonify({"message": "Nodes registered", "total": registry.count(), "nodes": registry.all()}), 201

    @api_v1.route("/nodes", methods=["GET"])
    async def v1_nodes_list():
        return jsonify({"nodes": registry.all(), "total": registry.count()}), 200

    @api_v1.route("/nodes/resolve", methods=["GET"])
    async def v1_nodes_resolve():
        replaced = consensus.resolve()
        chain = chain_service.chain_as_dicts()
        message = "Chain replaced with longer valid chain from network" if replaced else "Local chain is authoritative"
        logger.info("consensus_resolved", extra={"data": {"replaced": replaced, "chain_height": len(chain)}})
        return jsonify({"message": message, "replaced": replaced, "chain": chain}), 200

    @api_v1.route("/metrics", methods=["GET"])
    async def v1_metrics():
        return jsonify({
            "chain_height": chain_service.chain_length(),
            "pending_transactions": pool.count(),
            "avg_mine_time_seconds": chain_service.avg_mine_time_seconds(),
        }), 200

    @api_v1.route("/health", methods=["GET"])
    async def v1_health():
        chain_height = chain_service.chain_length()
        if dsn:
            db_ok = check_db_connectivity(dsn)
            db_status = "ok" if db_ok else "error"
            status = "ok" if db_ok else "degraded"
            http_code = 200 if db_ok else 503
        else:
            db_status = "n/a"
            status = "ok"
            http_code = 200
        return jsonify({"status": status, "db": db_status, "chain_height": chain_height}), http_code

    @api_v1.websocket("/ws")
    async def v1_ws():
        await hub.serve()

    @app.route("/mine_block", methods=["GET"])
    async def legacy_mine_block():
        return jsonify(_mine(chain_service, pool)), 200

    @app.route("/get_chain", methods=["GET"])
    async def legacy_get_chain():
        chain = chain_service.chain_as_dicts()
        return jsonify({"chain": chain, "length": len(chain)}), 200

    @app.route("/valid", methods=["GET"])
    async def legacy_valid():
        is_valid = chain_service.is_chain_valid()
        message = (
            "The Blockchain is valid." if is_valid else "The Blockchain is not valid."
        )
        return jsonify({"message": message}), 200

    app.register_blueprint(api_v1)
    register_error_handlers(app)
    return app


app = create_app()


if __name__ == "__main__":
    create_app(dsn=DATABASE_URL).run(host="127.0.0.1", port=5000)
