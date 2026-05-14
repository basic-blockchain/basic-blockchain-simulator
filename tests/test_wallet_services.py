"""Unit tests for domain wallet services: TransferService, MintService, apply_block_deltas.

Covers exchange rate handling, nonce validation, frozen wallet checks,
balance mutations, and cross-currency transfers.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from domain.crypto import (
    canonical_transfer_message,
    derive_keypair,
    generate_mnemonic,
    mnemonic_to_seed,
    public_key_hex,
    sign,
)
from domain.currency_repository import ExchangeRateRecord
from domain.wallet import (
    COINBASE_SIGNATURE,
    ExchangeRateNotFoundError,
    MintService,
    SignatureRejectedError,
    TransferService,
    WalletService,
    apply_block_deltas,
)
from domain.wallet_repository import (
    CurrencyMismatchError,
    InMemoryWalletStore,
    InsufficientBalanceError,
    NonceReplayError,
    WalletFrozenError,
    WalletNotFoundError,
    WalletType,
)


class InMemoryCurrencyStore:
    """Mock currency repository for testing."""

    def __init__(self):
        self._rates: list[ExchangeRateRecord] = []

    def add_rate(self, rate: ExchangeRateRecord) -> None:
        self._rates.append(rate)

    def list_exchange_rates(
        self,
        *,
        from_currency: str,
        to_currency: str,
        limit: int = 10,
    ) -> list[ExchangeRateRecord]:
        matching = [
            r
            for r in self._rates
            if r.from_currency == from_currency and r.to_currency == to_currency
        ]
        return matching[:limit]


def _create_keypair():
    """Helper to generate a test keypair."""
    mnemonic = generate_mnemonic()
    seed = mnemonic_to_seed(mnemonic)
    private_key, public_key = derive_keypair(seed)
    return private_key, public_key_hex(public_key), seed


# ── WalletService ──────────────────────────────────────────────────────


def test_wallet_service_create_wallet_generates_new_keypair():
    """Test WalletService.create_wallet generates mnemonic + keypair."""
    wallets = InMemoryWalletStore()
    service = WalletService(wallets)

    created = service.create_wallet(user_id="user_1", currency="NATIVE")

    assert created.wallet_id.startswith("w_")
    assert len(created.mnemonic.split()) == 12
    assert len(created.public_key) == 66  # 33-byte compressed hex
    assert wallets.get_wallet(created.wallet_id) is not None


def test_wallet_service_create_wallet_with_public_key():
    """Test WalletService.create_wallet_with_public_key uses provided key."""
    wallets = InMemoryWalletStore()
    service = WalletService(wallets)

    _, pub_hex, _ = _create_keypair()
    created = service.create_wallet_with_public_key(user_id="user_1", public_key=pub_hex)

    assert created.wallet_id.startswith("w_")
    assert created.public_key == pub_hex
    assert created.mnemonic == ""
    wallet = wallets.get_wallet(created.wallet_id)
    assert wallet is not None
    assert wallet.public_key == pub_hex


def test_wallet_service_get_wallet():
    """Test WalletService.get_wallet proxies to repository."""
    wallets = InMemoryWalletStore()
    service = WalletService(wallets)

    created = service.create_wallet(user_id="user_1")
    fetched = service.get_wallet(created.wallet_id)

    assert fetched is not None
    assert fetched.wallet_id == created.wallet_id
    assert fetched.balance == Decimal(0)


def test_wallet_service_list_user_wallets():
    """Test WalletService.list_user_wallets filters by user."""
    wallets = InMemoryWalletStore()
    service = WalletService(wallets)

    service.create_wallet(user_id="alice")
    service.create_wallet(user_id="alice")
    service.create_wallet(user_id="bob")

    alice_wallets = service.list_user_wallets("alice")
    bob_wallets = service.list_user_wallets("bob")

    assert len(alice_wallets) == 2
    assert len(bob_wallets) == 1


def test_wallet_service_set_frozen():
    """Test WalletService.set_frozen updates wallet state."""
    wallets = InMemoryWalletStore()
    service = WalletService(wallets)

    created = service.create_wallet(user_id="alice")
    service.set_frozen(wallet_id=created.wallet_id, frozen=True)

    wallet = service.get_wallet(created.wallet_id)
    assert wallet.frozen is True


# ── TransferService ────────────────────────────────────────────────────


def test_transfer_service_build_transaction_same_currency():
    """Test TransferService.build_transaction for same-currency transfer."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    # Create two wallets
    priv_alice, pub_hex_alice, _ = _create_keypair()
    pub_bob, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )

    # Fund alice
    wallets.credit(wallet_id="w_alice", amount=Decimal(100))

    # Build and sign transfer
    amount = Decimal(50)
    nonce = 1
    message = canonical_transfer_message(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
    )
    signature = sign(priv_alice, message)

    tx = service.build_transaction(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
        signature=signature,
        sender_username="alice",
        receiver_username="bob",
    )

    assert tx.sender == "alice"
    assert tx.receiver == "bob"
    assert tx.amount == amount
    assert tx.receiver_amount is None  # same currency


def test_transfer_service_build_transaction_sender_not_found():
    """Test TransferService raises WalletNotFoundError for missing sender."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    with pytest.raises(WalletNotFoundError):
        service.build_transaction(
            sender_wallet_id="w_nonexistent",
            receiver_wallet_id="w_bob",
            amount=Decimal(50),
            nonce=1,
            signature="fake_sig",
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_receiver_not_found():
    """Test TransferService raises WalletNotFoundError for missing receiver."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    _, pub_hex, _ = _create_keypair()
    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex,
    )

    with pytest.raises(WalletNotFoundError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_nonexistent",
            amount=Decimal(50),
            nonce=1,
            signature="fake_sig",
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_sender_frozen():
    """Test TransferService raises WalletFrozenError for frozen sender."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )
    wallets.set_frozen(wallet_id="w_alice", frozen=True)

    with pytest.raises(WalletFrozenError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=Decimal(50),
            nonce=1,
            signature="fake_sig",
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_receiver_frozen():
    """Test TransferService raises WalletFrozenError for frozen receiver."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )
    wallets.set_frozen(wallet_id="w_bob", frozen=True)

    with pytest.raises(WalletFrozenError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=Decimal(50),
            nonce=1,
            signature="fake_sig",
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_insufficient_balance():
    """Test TransferService raises InsufficientBalanceError."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )
    wallets.credit(wallet_id="w_alice", amount=Decimal(10))

    with pytest.raises(InsufficientBalanceError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=Decimal(50),
            nonce=1,
            signature="fake_sig",
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_nonce_replay():
    """Test TransferService rejects nonce <= last_used_nonce."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    priv_alice, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )
    wallets.credit(wallet_id="w_alice", amount=Decimal(100))
    wallets.reserve_nonce(wallet_id="w_alice", nonce=5)

    # Try to build with nonce <= 5
    amount = Decimal(50)
    nonce = 3
    message = canonical_transfer_message(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
    )
    signature = sign(priv_alice, message)

    with pytest.raises(NonceReplayError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=amount,
            nonce=nonce,
            signature=signature,
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_bad_signature():
    """Test TransferService rejects invalid signature."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )
    wallets.credit(wallet_id="w_alice", amount=Decimal(100))

    with pytest.raises(SignatureRejectedError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=Decimal(50),
            nonce=1,
            signature="0" * 128,  # invalid signature
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_cross_currency_no_rate():
    """Test TransferService raises ExchangeRateNotFoundError."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    priv_alice, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
        currency="USD",
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
        currency="EUR",
    )
    wallets.credit(wallet_id="w_alice", amount=Decimal(100))

    amount = Decimal(50)
    nonce = 1
    message = canonical_transfer_message(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
    )
    signature = sign(priv_alice, message)

    with pytest.raises(ExchangeRateNotFoundError):
        service.build_transaction(
            sender_wallet_id="w_alice",
            receiver_wallet_id="w_bob",
            amount=amount,
            nonce=nonce,
            signature=signature,
            sender_username="alice",
            receiver_username="bob",
        )


def test_transfer_service_build_transaction_cross_currency_with_rate():
    """Test TransferService applies exchange rate correctly."""
    wallets = InMemoryWalletStore()
    currencies = InMemoryCurrencyStore()
    service = TransferService(wallets, currencies)

    priv_alice, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
        currency="USD",
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
        currency="EUR",
    )
    wallets.credit(wallet_id="w_alice", amount=Decimal(1000))

    # USD 100 -> EUR at rate 0.92 (1 USD = 0.92 EUR), fee 2%
    # Gross: 100 * 0.92 = 92 EUR
    # Fee: 92 * 0.02 = 1.84 EUR
    # Net: 92 - 1.84 = 90.16 EUR
    rate = ExchangeRateRecord(
        rate_id=1,
        from_currency="USD",
        to_currency="EUR",
        rate=Decimal("0.92"),
        fee_rate=Decimal("0.02"),
        source="test",
        updated_at="2026-05-13T00:00:00Z",
    )
    currencies.add_rate(rate)

    amount = Decimal(100)
    nonce = 1
    message = canonical_transfer_message(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
    )
    signature = sign(priv_alice, message)

    tx = service.build_transaction(
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        amount=amount,
        nonce=nonce,
        signature=signature,
        sender_username="alice",
        receiver_username="bob",
    )

    assert tx.amount == Decimal(100)
    assert tx.receiver_amount == Decimal("90.16000000")


# ── MintService ────────────────────────────────────────────────────────


def test_mint_service_build_mint_success():
    """Test MintService.build_mint creates coinbase transaction."""
    wallets = InMemoryWalletStore()
    service = MintService(wallets)

    _, pub_hex, _ = _create_keypair()
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex,
    )

    tx = service.build_mint(
        receiver_wallet_id="w_bob",
        amount=Decimal(100),
        receiver_username="bob",
        admin_username="admin",
    )

    assert tx.sender == "COINBASE(admin)"
    assert tx.receiver == "bob"
    assert tx.amount == Decimal(100)
    assert tx.receiver_wallet_id == "w_bob"
    assert tx.sender_wallet_id == ""
    assert tx.nonce == 0
    assert tx.signature == COINBASE_SIGNATURE


def test_mint_service_build_mint_wallet_not_found():
    """Test MintService raises WalletNotFoundError for missing wallet."""
    wallets = InMemoryWalletStore()
    service = MintService(wallets)

    with pytest.raises(WalletNotFoundError):
        service.build_mint(
            receiver_wallet_id="w_nonexistent",
            amount=Decimal(100),
            receiver_username="bob",
            admin_username="admin",
        )


def test_mint_service_build_mint_receiver_frozen():
    """Test MintService raises WalletFrozenError for frozen wallet."""
    wallets = InMemoryWalletStore()
    service = MintService(wallets)

    _, pub_hex, _ = _create_keypair()
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex,
    )
    wallets.set_frozen(wallet_id="w_bob", frozen=True)

    with pytest.raises(WalletFrozenError):
        service.build_mint(
            receiver_wallet_id="w_bob",
            amount=Decimal(100),
            receiver_username="bob",
            admin_username="admin",
        )


def test_mint_service_build_mint_non_positive_amount():
    """Test MintService rejects non-positive amounts."""
    wallets = InMemoryWalletStore()
    service = MintService(wallets)

    _, pub_hex, _ = _create_keypair()
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex,
    )

    with pytest.raises(ValueError, match="positive"):
        service.build_mint(
            receiver_wallet_id="w_bob",
            amount=Decimal(0),
            receiver_username="bob",
            admin_username="admin",
        )


# ── apply_block_deltas ─────────────────────────────────────────────────


def test_apply_block_deltas_coinbase_transaction():
    """Test apply_block_deltas credits receiver for coinbase transactions."""
    from domain.models import Transaction

    wallets = InMemoryWalletStore()

    _, pub_hex, _ = _create_keypair()
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex,
    )
    wallets.credit(wallet_id="w_bob", amount=Decimal(100))

    tx = Transaction(
        sender="COINBASE(admin)",
        receiver="bob",
        amount=Decimal(50),
        receiver_amount=None,
        sender_wallet_id="",
        receiver_wallet_id="w_bob",
        nonce=0,
        signature=COINBASE_SIGNATURE,
    )

    apply_block_deltas(wallets, [tx])

    wallet = wallets.get_wallet("w_bob")
    assert wallet.balance == Decimal(150)  # 100 + 50


def test_apply_block_deltas_regular_transfer():
    """Test apply_block_deltas debits sender and credits receiver."""
    from domain.models import Transaction

    wallets = InMemoryWalletStore()

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
    )

    wallets.credit(wallet_id="w_alice", amount=Decimal(100))
    wallets.credit(wallet_id="w_bob", amount=Decimal(50))

    tx = Transaction(
        sender="alice",
        receiver="bob",
        amount=Decimal(30),
        receiver_amount=None,
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        nonce=1,
        signature="dummy_sig",
    )

    apply_block_deltas(wallets, [tx])

    alice_wallet = wallets.get_wallet("w_alice")
    bob_wallet = wallets.get_wallet("w_bob")

    assert alice_wallet.balance == Decimal(70)  # 100 - 30
    assert bob_wallet.balance == Decimal(80)  # 50 + 30


def test_apply_block_deltas_legacy_transaction():
    """Test apply_block_deltas skips balance update for legacy tx."""
    from domain.models import Transaction

    wallets = InMemoryWalletStore()

    tx = Transaction(
        sender="alice",
        receiver="bob",
        amount=Decimal(30),
        receiver_amount=None,
        sender_wallet_id="",  # legacy: no wallet IDs
        receiver_wallet_id="",
        nonce=1,
        signature="dummy_sig",
    )

    # Should not raise; just ignores balance changes
    apply_block_deltas(wallets, [tx])


def test_apply_block_deltas_cross_currency_transfer():
    """Test apply_block_deltas handles receiver_amount for cross-currency."""
    from domain.models import Transaction

    wallets = InMemoryWalletStore()

    _, pub_hex_alice, _ = _create_keypair()
    _, pub_hex_bob, _ = _create_keypair()

    wallets.create_wallet(
        wallet_id="w_alice",
        user_id="user_alice",
        public_key=pub_hex_alice,
        currency="USD",
    )
    wallets.create_wallet(
        wallet_id="w_bob",
        user_id="user_bob",
        public_key=pub_hex_bob,
        currency="EUR",
    )

    wallets.credit(wallet_id="w_alice", amount=Decimal(100))
    wallets.credit(wallet_id="w_bob", amount=Decimal(0))

    tx = Transaction(
        sender="alice",
        receiver="bob",
        amount=Decimal(100),  # USD
        receiver_amount=Decimal("90.16"),  # EUR (after exchange + fee)
        sender_wallet_id="w_alice",
        receiver_wallet_id="w_bob",
        nonce=1,
        signature="dummy_sig",
    )

    apply_block_deltas(wallets, [tx])

    alice_wallet = wallets.get_wallet("w_alice")
    bob_wallet = wallets.get_wallet("w_bob")

    assert alice_wallet.balance == Decimal(0)  # 100 - 100
    assert bob_wallet.balance == Decimal("90.16")  # 0 + 90.16
