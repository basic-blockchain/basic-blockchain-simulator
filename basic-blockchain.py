from __future__ import annotations

import asyncio
import uuid

from dotenv import load_dotenv

load_dotenv()

from quart import Blueprint, Quart, g, jsonify, request

from api.admin_routes import build_admin_blueprint
from api.auth_middleware import install_auth_middleware
from api.auth_routes import build_auth_blueprint
from api.errors import bad_request, register_error_handlers
from api.permissions import set_permission_resolver
from api.wallet_routes import build_wallet_blueprint
from api.kyc_admin_routes import build_kyc_admin_blueprint
from api.kyc_routes import build_kyc_blueprint
from api.health import check_db_connectivity
from api.logging_config import logger
from api.rate_limit import rate_limit
from api.schemas import parse_currency_code, parse_transaction
from api.websocket_hub import WebSocketHub
from config import (
    BCRYPT_ROUNDS,
    BOOTSTRAP_ADMIN_USERNAME,
    DATABASE_URL,
    DIFFICULTY_PREFIX,
    EXCHANGE_FEED_ENABLED,
    EXCHANGE_FEED_INTERVAL_SECONDS,
    EXCHANGE_FEED_PAIRS,
    EXCHANGE_FEED_PROVIDER,
    JWT_ALGORITHM,
    JWT_SECRET,
    JWT_TTL_SECONDS,
    TESTING,
)
from domain import BlockchainService, ConsensusService, InMemoryNodeRegistry, MempoolService, PropagationService
from domain.currency_repository import CurrencyRepositoryProtocol, InMemoryCurrencyStore
from domain.user_repository import InMemoryUserStore, UserRepositoryProtocol
from domain.wallet_repository import InMemoryWalletStore, WalletRepositoryProtocol
from infrastructure.postgres_currency_store import PostgresCurrencyStore
from infrastructure.postgres_mempool_repository import PostgresMempoolRepository
from infrastructure.postgres_node_registry import PostgresNodeRegistry
from infrastructure.postgres_repository import PostgresBlockRepository
from infrastructure.postgres_user_store import PostgresUserStore
from infrastructure.postgres_wallet_store import PostgresWalletStore
from infrastructure.exchange_rate_sync import (
    ExchangeRateSyncError,
    ExchangeRateSyncPair,
    PROVIDER_BINANCE,
    PROVIDER_CRYPTO_COM,
    sync_exchange_rates,
)


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
            "auth_register": "/api/v1/auth/register",
            "auth_activate": "/api/v1/auth/activate",
            "auth_login": "/api/v1/auth/login",
            "auth_me": "/api/v1/auth/me",
            "admin_users": "/api/v1/admin/users",
            "admin_user_update": "/api/v1/admin/users/<id>",
            "admin_user_delete": "/api/v1/admin/users/<id>",
            "admin_user_restore": "/api/v1/admin/users/<id>/restore",
            "admin_roles": "/api/v1/admin/users/<id>/roles",
            "admin_ban": "/api/v1/admin/users/<id>/ban",
            "admin_unban": "/api/v1/admin/users/<id>/unban",
            "admin_permissions": "/api/v1/admin/users/<id>/permissions",
            "admin_audit": "/api/v1/admin/audit",
            "admin_mint": "/api/v1/admin/mint",
            "admin_wallets": "/api/v1/admin/wallets",
            "admin_wallet_freeze": "/api/v1/admin/wallets/<id>/freeze",
            "admin_wallet_unfreeze": "/api/v1/admin/wallets/<id>/unfreeze",
            "admin_wallet_top_up": "/api/v1/admin/wallets/<id>/top-up",
            "admin_currencies": "/api/v1/admin/currencies",
            "admin_treasury": "/api/v1/admin/treasury",
            "admin_exchange_rates": "/api/v1/admin/exchange-rates",
            "admin_exchange_rates_sync": "/api/v1/admin/exchange-rates/sync",
            "admin_stats": "/api/v1/admin/stats",
            "wallets_create": "/api/v1/wallets",
            "wallets_me": "/api/v1/wallets/me",
            "currencies": "/api/v1/currencies",
            "transactions_signed": "/api/v1/transactions/signed",
            "health": "/api/v1/health",
            "metrics": "/api/v1/metrics",
            "nodes_register": "/api/v1/nodes/register",
            "nodes": "/api/v1/nodes",
            "nodes_resolve": "/api/v1/nodes/resolve",
            "ws": "/api/v1/ws",
        },
    }


def _mine(
    blockchain: BlockchainService,
    mempool: MempoolService,
    wallet_repo=None,
) -> dict[str, object]:
    previous_block = blockchain.previous_block()
    previous_proof = previous_block.proof
    proof = blockchain.proof_of_work(previous_proof)
    previous_hash = blockchain.hash_block(previous_block)
    # Flush mempool BEFORE creating the block so the block can be stamped with
    # its actual transactions and Merkle root. `create_block` (via the repo's
    # `append`) persists block + transactions atomically — no separate
    # save_confirmed_transactions call needed.
    included = mempool.flush()
    block = blockchain.create_block(proof, previous_hash, transactions=included)
    # Phase I.3 — apply balance deltas for every confirmed transaction.
    # Coinbase txs credit the receiver; transfers debit sender + credit
    # receiver. The wallet repo is None in pure in-memory mode (legacy
    # tests that never mint a wallet) — in that case the deltas are
    # skipped because there are no wallets to mutate.
    if wallet_repo is not None and included:
        from domain.wallet import apply_block_deltas

        apply_block_deltas(wallet_repo, included)
    included_dicts = [tx.to_dict() for tx in included]
    logger.info(
        "block_mined",
        extra={"data": {"index": block.index, "proof": block.proof, "tx_count": len(included_dicts)}},
    )
    # The top-level `transactions` field is kept for back-compat with v0.9.0
    # clients; the same list is also nested under each block (via to_dict)
    # whenever blocks are returned by /chain or block detail.
    return {
        "message": "A block is MINED",
        "index": block.index,
        "timestamp": block.timestamp,
        "proof": block.proof,
        "previous_hash": block.previous_hash,
        "merkle_root": block.merkle_root,
        "transactions": included_dicts,
    }


def create_app(
    blockchain: BlockchainService | None = None,
    mempool: MempoolService | None = None,
    dsn: str | None = None,
    node_registry=None,
    propagation: PropagationService | None = None,
    ws_hub: WebSocketHub | None = None,
    users: UserRepositoryProtocol | None = None,
    wallets: WalletRepositoryProtocol | None = None,
) -> Quart:
    app = Quart(__name__)
    if dsn:
        wallet_store: WalletRepositoryProtocol = wallets or PostgresWalletStore(dsn)
        currency_store: CurrencyRepositoryProtocol = PostgresCurrencyStore(dsn)
        chain_service = blockchain or BlockchainService(
            repository=PostgresBlockRepository(dsn),
            difficulty_prefix=DIFFICULTY_PREFIX,
            wallet_repo=wallet_store,
        )
        pool = mempool or MempoolService(repository=PostgresMempoolRepository(dsn))
        registry = node_registry or PostgresNodeRegistry(dsn)
        user_store: UserRepositoryProtocol = users or PostgresUserStore(dsn)
    else:
        wallet_store = wallets or InMemoryWalletStore()
        currency_store = InMemoryCurrencyStore()
        chain_service = blockchain or BlockchainService(
            difficulty_prefix=DIFFICULTY_PREFIX,
            wallet_repo=wallet_store,
        )
        pool = mempool or MempoolService()
        registry = node_registry or InMemoryNodeRegistry()
        user_store = users or InMemoryUserStore()

    consensus = ConsensusService(blockchain=chain_service, registry=registry)
    propagator = propagation or PropagationService(registry=registry)
    hub = ws_hub or WebSocketHub()

    # Phase I.1: install JWT middleware. The middleware no-ops on PUBLIC_PATHS
    # and rejects malformed/expired tokens with 401 before the route runs.
    if not JWT_SECRET and not TESTING:
        raise RuntimeError(
            "JWT_SECRET is required outside TESTING mode. "
            "Set it in the environment before starting the simulator."
        )
    install_auth_middleware(app, secret=JWT_SECRET, algorithm=JWT_ALGORITHM)

    @app.before_request
    async def _assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    def _parse_exchange_feed_pairs(
        *,
        raw_pairs: str,
        currencies: CurrencyRepositoryProtocol,
    ) -> list[ExchangeRateSyncPair]:
        pairs: list[ExchangeRateSyncPair] = []
        if not raw_pairs.strip():
            return pairs
        for entry in [p.strip() for p in raw_pairs.split(",") if p.strip()]:
            if "/" not in entry:
                raise ValueError("pairs must use FROM/TO format (example: BTC/USDT)")
            raw_from, raw_to = [part.strip() for part in entry.split("/", 1)]
            from_currency = parse_currency_code(raw_from)
            to_currency = parse_currency_code(raw_to)
            if from_currency == to_currency:
                raise ValueError("Currencies must differ")
            if currencies.get_currency(from_currency) is None or currencies.get_currency(to_currency) is None:
                raise ValueError("Currency not found")
            pairs.append(
                ExchangeRateSyncPair(from_currency=from_currency, to_currency=to_currency)
            )
        return pairs

    async def _exchange_feed_loop(
        *,
        pairs: list[ExchangeRateSyncPair],
        provider: str,
        interval_seconds: int,
    ) -> None:
        while True:
            try:
                await asyncio.to_thread(
                    sync_exchange_rates,
                    currencies=currency_store,
                    pairs=pairs,
                    provider=provider,
                )
                logger.info(
                    "exchange_feed_synced",
                    extra={
                        "data": {
                            "provider": provider,
                            "pairs": [f"{p.from_currency}/{p.to_currency}" for p in pairs],
                        }
                    },
                )
            except ExchangeRateSyncError as exc:
                logger.warning(
                    "exchange_feed_failed",
                    extra={"data": {"provider": provider, "error": str(exc)}},
                )
            await asyncio.sleep(interval_seconds)

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
        result = _mine(chain_service, pool, wallet_repo=wallet_store)
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
        return jsonify(_mine(chain_service, pool, wallet_repo=wallet_store)), 200

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

    # Phase I.1: nest the auth blueprint under /api/v1 so its routes resolve
    # to /api/v1/auth/register, /api/v1/auth/login, etc.
    auth_bp = build_auth_blueprint(
        users=user_store,
        jwt_secret=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        jwt_ttl_seconds=JWT_TTL_SECONDS,
        bcrypt_rounds=BCRYPT_ROUNDS,
        bootstrap_admin_username=BOOTSTRAP_ADMIN_USERNAME,
    )
    api_v1.register_blueprint(auth_bp)

    # Phase I.2: admin endpoints + RBAC resolvers. The decorator reads
    # `g.current_user` populated by the auth middleware and falls back to
    # the static defaults in `domain/permissions.py` if these loaders are
    # not registered.
    set_permission_resolver(
        role_overrides=user_store.get_role_overrides,
        user_overrides=user_store.get_user_overrides,
    )
    admin_bp = build_admin_blueprint(
        users=user_store,
        wallets=wallet_store,
        currencies=currency_store,
        blockchain=chain_service,
        bcrypt_rounds=BCRYPT_ROUNDS,
    )
    api_v1.register_blueprint(admin_bp)

    # Phase I.3: wallet endpoints (create, list-mine, signed transfer,
    # admin mint). Each route is gated by `@require_permission(...)`.
    wallet_bp = build_wallet_blueprint(
        wallets=wallet_store,
        users=user_store,
        mempool=pool,
        currencies=currency_store,
    )
    api_v1.register_blueprint(wallet_bp)

    # Phase 6g: KYC user-flow endpoints (/me/kyc/status, /documents,
    # /review). Any authenticated user can manage their own KYC state;
    # admin-side review/approval lives elsewhere.
    kyc_bp = build_kyc_blueprint(users=user_store)
    api_v1.register_blueprint(kyc_bp)

    # Phase 6g-admin: KYC admin review endpoints (/admin/kyc/*). Gated by
    # Permission.REVIEW_KYC which is on the ADMIN baseline.
    kyc_admin_bp = build_kyc_admin_blueprint(users=user_store)
    api_v1.register_blueprint(kyc_admin_bp)

    @app.before_serving
    async def _start_exchange_feed():
        if TESTING or not EXCHANGE_FEED_ENABLED:
            return
        provider = EXCHANGE_FEED_PROVIDER.strip().upper() or PROVIDER_BINANCE
        if provider not in {PROVIDER_BINANCE, PROVIDER_CRYPTO_COM}:
            logger.warning(
                "exchange_feed_disabled",
                extra={"data": {"reason": "unsupported provider", "provider": provider}},
            )
            return
        try:
            pairs = _parse_exchange_feed_pairs(
                raw_pairs=EXCHANGE_FEED_PAIRS,
                currencies=currency_store,
            )
        except ValueError as exc:
            logger.warning(
                "exchange_feed_disabled",
                extra={"data": {"reason": str(exc), "pairs": EXCHANGE_FEED_PAIRS}},
            )
            return
        if not pairs:
            logger.warning(
                "exchange_feed_disabled",
                extra={"data": {"reason": "no pairs configured"}},
            )
            return
        interval = max(30, EXCHANGE_FEED_INTERVAL_SECONDS)
        app.add_background_task(
            _exchange_feed_loop,
            pairs=pairs,
            provider=provider,
            interval_seconds=interval,
        )

    app.register_blueprint(api_v1)
    register_error_handlers(app)
    return app


app = create_app()


if __name__ == "__main__":
    create_app(dsn=DATABASE_URL).run(host="127.0.0.1", port=5000)
