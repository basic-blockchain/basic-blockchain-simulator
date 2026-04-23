from __future__ import annotations

from flask import Blueprint, Flask, jsonify, request

from api.errors import bad_request, register_error_handlers
from api.schemas import parse_transaction
from config import DIFFICULTY_PREFIX
from domain import BlockchainService, MempoolService


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
        },
    }


def _mine(blockchain: BlockchainService, mempool: MempoolService) -> dict[str, object]:
    previous_block = blockchain.previous_block()
    previous_proof = previous_block.proof
    proof = blockchain.proof_of_work(previous_proof)
    previous_hash = blockchain.hash_block(previous_block)
    block = blockchain.create_block(proof, previous_hash)
    included = [tx.to_dict() for tx in mempool.flush()]
    return {
        "message": "A block is MINED",
        "index": block.index,
        "timestamp": block.timestamp,
        "proof": block.proof,
        "previous_hash": block.previous_hash,
        "transactions": included,
    }


def create_app(
    blockchain: BlockchainService | None = None,
    mempool: MempoolService | None = None,
) -> Flask:
    app = Flask(__name__)
    chain_service = blockchain or BlockchainService(difficulty_prefix=DIFFICULTY_PREFIX)
    pool = mempool or MempoolService()

    api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    @app.route("/", methods=["GET"])
    def home():
        return jsonify(_legacy_home_payload()), 200

    @api_v1.route("/", methods=["GET"])
    def v1_home():
        return jsonify(_v1_home_payload()), 200

    @api_v1.route("/mine_block", methods=["POST"])
    def v1_mine_block():
        return jsonify(_mine(chain_service, pool)), 200

    @api_v1.route("/chain", methods=["GET"])
    def v1_chain():
        chain = chain_service.chain_as_dicts()
        return jsonify({"chain": chain, "length": len(chain)}), 200

    @api_v1.route("/valid", methods=["GET"])
    def v1_valid():
        is_valid = chain_service.is_chain_valid()
        message = (
            "The Blockchain is valid." if is_valid else "The Blockchain is not valid."
        )
        return jsonify({"message": message, "valid": is_valid}), 200

    @api_v1.route("/transactions", methods=["POST"])
    def v1_add_transaction():
        try:
            tx = parse_transaction(request)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        try:
            pool.add(tx)
        except ValueError as exc:
            return bad_request(str(exc), "VALIDATION_ERROR")
        return jsonify({"message": "Transaction added", "transaction": tx.to_dict()}), 201

    @api_v1.route("/transactions/pending", methods=["GET"])
    def v1_pending_transactions():
        pending = [tx.to_dict() for tx in pool.pending()]
        return jsonify({"transactions": pending, "count": len(pending)}), 200

    @app.route("/mine_block", methods=["GET"])
    def legacy_mine_block():
        return jsonify(_mine(chain_service, pool)), 200

    @app.route("/get_chain", methods=["GET"])
    def legacy_get_chain():
        chain = chain_service.chain_as_dicts()
        return jsonify({"chain": chain, "length": len(chain)}), 200

    @app.route("/valid", methods=["GET"])
    def legacy_valid():
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
    create_app().run(host="127.0.0.1", port=5000)
