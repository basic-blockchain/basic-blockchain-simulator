import pytest

from domain import BlockchainService, MempoolService, Transaction
from infrastructure.postgres_mempool_repository import PostgresMempoolRepository
from infrastructure.postgres_repository import PostgresBlockRepository


pytestmark = pytest.mark.integration


def test_genesis_block_persisted_on_first_init(clean_db):
    repo = PostgresBlockRepository(clean_db)
    BlockchainService(repository=repo)

    assert repo.count() == 1
    genesis = repo.get_all()[0]
    assert genesis.index == 1
    assert genesis.previous_hash == "0"


def test_second_init_does_not_duplicate_genesis(clean_db):
    repo = PostgresBlockRepository(clean_db)
    BlockchainService(repository=repo)
    BlockchainService(repository=repo)  # second init must not re-insert genesis

    assert repo.count() == 1


def test_mined_block_survives_new_service_instance(clean_db):
    repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=repo, difficulty_prefix="0")
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))

    # New service reattaches to existing data
    repo2 = PostgresBlockRepository(clean_db)
    svc2 = BlockchainService(repository=repo2, difficulty_prefix="0")

    assert repo2.count() == 2
    assert svc2.is_chain_valid()


def test_chain_validity_across_repository_instances(clean_db):
    repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=repo, difficulty_prefix="0")

    for _ in range(2):
        prev = svc.previous_block()
        proof = svc.proof_of_work(prev.proof)
        svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))

    fresh_repo = PostgresBlockRepository(clean_db)
    fresh_svc = BlockchainService(repository=fresh_repo, difficulty_prefix="0")
    assert fresh_svc.is_chain_valid()
    assert fresh_repo.count() == 3


def test_tampered_block_detected_after_reload(clean_db):
    repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=repo, difficulty_prefix="0")
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))

    # Tamper directly via a second repo handle
    import psycopg2
    conn = psycopg2.connect(clean_db)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE blocks SET previous_hash = 'tampered' WHERE index = 2")
    conn.close()

    fresh_repo = PostgresBlockRepository(clean_db)
    fresh_svc = BlockchainService(repository=fresh_repo, difficulty_prefix="0")
    assert fresh_svc.is_chain_valid() is False


# ── Mempool persistence ────────────────────────────────────────────────────


def test_mempool_add_survives_new_service_instance(clean_db):
    repo = PostgresMempoolRepository(clean_db)
    svc = MempoolService(repository=repo)
    svc.add(Transaction(sender="alice", receiver="bob", amount=1.5))

    repo2 = PostgresMempoolRepository(clean_db)
    svc2 = MempoolService(repository=repo2)
    assert svc2.count() == 1
    pending = svc2.pending()
    assert pending[0].sender == "alice"
    assert pending[0].receiver == "bob"
    assert pending[0].amount == 1.5


def test_mempool_flush_clears_db(clean_db):
    repo = PostgresMempoolRepository(clean_db)
    svc = MempoolService(repository=repo)
    svc.add(Transaction(sender="alice", receiver="bob", amount=2.0))
    svc.add(Transaction(sender="carol", receiver="dave", amount=3.0))

    flushed = svc.flush()
    assert len(flushed) == 2

    assert PostgresMempoolRepository(clean_db).count() == 0


def test_mempool_flush_returns_fifo_order(clean_db):
    repo = PostgresMempoolRepository(clean_db)
    svc = MempoolService(repository=repo)
    svc.add(Transaction(sender="a", receiver="b", amount=1.0))
    svc.add(Transaction(sender="c", receiver="d", amount=2.0))
    svc.add(Transaction(sender="e", receiver="f", amount=3.0))

    flushed = svc.flush()
    assert [tx.sender for tx in flushed] == ["a", "c", "e"]


def test_mempool_count_reflects_db_state(clean_db):
    repo = PostgresMempoolRepository(clean_db)
    svc = MempoolService(repository=repo)
    assert svc.count() == 0
    svc.add(Transaction(sender="x", receiver="y", amount=0.5))
    assert PostgresMempoolRepository(clean_db).count() == 1


# ── Confirmed transactions persistence ────────────────────────────────────────


def test_confirmed_transactions_written_on_mine(clean_db):
    import psycopg2

    block_repo = PostgresBlockRepository(clean_db)
    mempool_repo = PostgresMempoolRepository(clean_db)
    svc = BlockchainService(repository=block_repo, difficulty_prefix="0")
    pool = MempoolService(repository=mempool_repo)

    pool.add(Transaction(sender="alice", receiver="bob", amount=5.0))
    pool.add(Transaction(sender="carol", receiver="dave", amount=3.0))

    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    block = svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))
    txs = pool.flush()
    svc.save_confirmed_transactions(block.index, txs)

    conn = psycopg2.connect(clean_db)
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sender, receiver, amount::float FROM transactions WHERE block_index = %s ORDER BY id",
            (block.index,),
        )
        rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0] == ("alice", "bob", 5.0)
    assert rows[1] == ("carol", "dave", 3.0)


def test_no_transactions_written_when_mempool_empty(clean_db):
    import psycopg2

    block_repo = PostgresBlockRepository(clean_db)
    svc = BlockchainService(repository=block_repo, difficulty_prefix="0")

    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    block = svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))
    svc.save_confirmed_transactions(block.index, [])

    conn = psycopg2.connect(clean_db)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM transactions WHERE block_index = %s", (block.index,))
        count = cur.fetchone()[0]
    conn.close()

    assert count == 0


def test_transactions_survive_service_restart(clean_db):
    import psycopg2

    block_repo = PostgresBlockRepository(clean_db)
    mempool_repo = PostgresMempoolRepository(clean_db)
    svc = BlockchainService(repository=block_repo, difficulty_prefix="0")
    pool = MempoolService(repository=mempool_repo)

    pool.add(Transaction(sender="eve", receiver="frank", amount=10.0))
    prev = svc.previous_block()
    proof = svc.proof_of_work(prev.proof)
    block = svc.create_block(proof=proof, previous_hash=svc.hash_block(prev))
    txs = pool.flush()
    svc.save_confirmed_transactions(block.index, txs)

    # Simulate restart — fresh repo instances
    conn = psycopg2.connect(clean_db)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT sender, receiver FROM transactions ORDER BY id")
        rows = cur.fetchall()
    conn.close()

    assert rows == [("eve", "frank")]
