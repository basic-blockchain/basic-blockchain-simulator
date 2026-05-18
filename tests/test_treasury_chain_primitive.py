"""Unit tests for the Phase 7.8.2.5 treasury chain primitive (BR-WL-10).

Covers the two domain-layer changes:

  * `apply_block_deltas` debits a treasury sender and credits a user
    recipient atomically when the transaction carries
    `TREASURY_SIGNATURE`.
  * `_validate_blocks` (via `BlockchainService.is_chain_valid()`) skips
    ECDSA verification for `TREASURY_SIGNATURE`, exactly like it does
    for `COINBASE_SIGNATURE`.
"""

from __future__ import annotations

from decimal import Decimal

from domain import BlockchainService, InMemoryBlockRepository, MempoolService
from domain.mempool_repository import InMemoryMempoolRepository
from domain.models import Transaction
from domain.wallet import (
    COINBASE_SIGNATURE,
    TREASURY_SIGNATURE,
    apply_block_deltas,
)
from domain.wallet_repository import InMemoryWalletStore


def _seed_wallets():
    wallets = InMemoryWalletStore()
    wallets.create_wallet(
        wallet_id="wal_treasury",
        user_id="SYSTEM",
        public_key="00" * 33,  # dummy compressed-pubkey-shaped string
        currency="NATIVE",
        wallet_type="TREASURY",
    )
    wallets.create_wallet(
        wallet_id="wal_alice",
        user_id="usr_alice",
        public_key="01" * 33,
        currency="NATIVE",
        wallet_type="USER",
    )
    return wallets


def test_apply_block_deltas_treasury_signature_debits_sender_credits_receiver():
    wallets = _seed_wallets()
    # Seed the treasury with funds via a coinbase tx.
    apply_block_deltas(
        wallets,
        [
            Transaction(
                sender="COINBASE",
                receiver="treasury",
                amount=Decimal("500"),
                receiver_wallet_id="wal_treasury",
                signature=COINBASE_SIGNATURE,
            )
        ],
    )
    assert wallets.get_wallet("wal_treasury").balance == Decimal("500")
    assert wallets.get_wallet("wal_alice").balance == Decimal("0")

    # Treasury → alice distribution transfer.
    apply_block_deltas(
        wallets,
        [
            Transaction(
                sender="TREASURY",
                receiver="alice",
                amount=Decimal("120"),
                sender_wallet_id="wal_treasury",
                receiver_wallet_id="wal_alice",
                signature=TREASURY_SIGNATURE,
            )
        ],
    )

    assert wallets.get_wallet("wal_treasury").balance == Decimal("380")
    assert wallets.get_wallet("wal_alice").balance == Decimal("120")


def test_is_chain_valid_accepts_treasury_signature_without_ecdsa():
    """A block containing a TREASURY_SIGNATURE tx remains valid even
    though the dummy public key cannot satisfy `verify(...)` —
    `_validate_blocks` must short-circuit exactly as it does for
    coinbase."""
    wallets = _seed_wallets()
    block_repo = InMemoryBlockRepository()
    chain = BlockchainService(repository=block_repo, wallet_repo=wallets)

    # Mine a block carrying one treasury transfer. We bypass the
    # service-level signature check by appending directly to the
    # mempool repo and mining.
    mempool = MempoolService(repository=InMemoryMempoolRepository())
    mempool.add(
        Transaction(
            sender="COINBASE",
            receiver="treasury",
            amount=Decimal("500"),
            receiver_wallet_id="wal_treasury",
            signature=COINBASE_SIGNATURE,
        )
    )
    mempool.add(
        Transaction(
            sender="TREASURY",
            receiver="alice",
            amount=Decimal("120"),
            sender_wallet_id="wal_treasury",
            receiver_wallet_id="wal_alice",
            signature=TREASURY_SIGNATURE,
        )
    )

    previous = chain.chain[-1]
    new_proof = chain.proof_of_work(previous.proof)
    chain.create_block(
        proof=new_proof,
        previous_hash=chain.hash_block(previous),
        transactions=mempool.flush(),
    )

    assert chain.is_chain_valid() is True


def test_is_chain_valid_still_rejects_invalid_regular_signature():
    """Sanity check that the new short-circuit did not loosen the
    real-signature path: a regular transfer with a bogus signature
    still fails."""
    wallets = _seed_wallets()
    block_repo = InMemoryBlockRepository()
    chain = BlockchainService(repository=block_repo, wallet_repo=wallets)

    mempool = MempoolService(repository=InMemoryMempoolRepository())
    mempool.add(
        Transaction(
            sender="alice",
            receiver="treasury",
            amount=Decimal("1"),
            sender_wallet_id="wal_alice",
            receiver_wallet_id="wal_treasury",
            nonce=1,
            signature="deadbeef",  # not a valid ECDSA signature for wal_alice
        )
    )

    previous = chain.chain[-1]
    new_proof = chain.proof_of_work(previous.proof)
    chain.create_block(
        proof=new_proof,
        previous_hash=chain.hash_block(previous),
        transactions=mempool.flush(),
    )

    assert chain.is_chain_valid() is False
