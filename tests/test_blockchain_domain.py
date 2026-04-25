from domain import Block, BlockchainService, InMemoryBlockRepository, Transaction


def test_genesis_block_exists_on_init():
    chain = BlockchainService()

    assert len(chain.chain) == 1
    assert chain.chain[0].index == 1
    assert chain.chain[0].previous_hash == "0"


def test_mining_flow_creates_valid_second_block():
    chain = BlockchainService()

    previous_block = chain.previous_block()
    proof = chain.proof_of_work(previous_block.proof)
    previous_hash = chain.hash_block(previous_block)
    chain.create_block(proof=proof, previous_hash=previous_hash)

    assert len(chain.chain) == 2
    assert chain.is_chain_valid() is True


def test_tampered_chain_becomes_invalid():
    chain = BlockchainService()

    previous_block = chain.previous_block()
    proof = chain.proof_of_work(previous_block.proof)
    previous_hash = chain.hash_block(previous_block)
    chain.create_block(proof=proof, previous_hash=previous_hash)

    chain.chain[1].previous_hash = "invalid_hash"

    assert chain.is_chain_valid() is False


def test_transaction_serializes_to_dict():
    tx = Transaction(sender="alice", receiver="bob", amount=42.5)

    assert tx.to_dict() == {"sender": "alice", "receiver": "bob", "amount": 42.5}


def test_transaction_amount_is_decimal():
    from decimal import Decimal

    tx = Transaction(sender="alice", receiver="bob", amount=42.5)

    assert isinstance(tx.amount, Decimal)
    assert tx.amount == Decimal("42.5")


def test_transaction_float_coerced_without_precision_loss():
    from decimal import Decimal

    tx = Transaction(sender="a", receiver="b", amount=0.1)

    assert tx.amount == Decimal("0.1")
    assert tx.amount != Decimal(0.1)  # Decimal(float) preserves the imprecision


def test_transaction_decimal_passthrough():
    from decimal import Decimal

    tx = Transaction(sender="a", receiver="b", amount=Decimal("99.99999999"))

    assert tx.amount == Decimal("99.99999999")
    assert isinstance(tx.amount, Decimal)


def test_custom_repository_is_used_by_chain_service():
    repo = InMemoryBlockRepository()
    chain = BlockchainService(repository=repo)

    assert chain.chain is repo.get_all()
    assert repo.count() == 1


def test_difficulty_prefix_is_configurable():
    chain = BlockchainService(difficulty_prefix="0")

    previous_block = chain.previous_block()
    proof = chain.proof_of_work(previous_block.proof)
    previous_hash = chain.hash_block(previous_block)
    chain.create_block(proof=proof, previous_hash=previous_hash)

    assert chain.is_chain_valid() is True
