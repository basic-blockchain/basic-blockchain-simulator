import pytest

from domain import BlockchainService
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
