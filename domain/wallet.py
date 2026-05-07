"""Wallet services (Phase I.3).

Three thin services over the wallet repository:

- `WalletService.create_wallet` generates a fresh BIP-39 mnemonic,
  derives a secp256k1 keypair from it, persists ONLY the public key,
  and returns `(wallet_id, public_key, mnemonic)`. The caller (HTTP
  layer) must show the mnemonic to the user once and discard it.
- `TransferService.build_transaction` validates a signed transfer
  (signature + nonce monotonicity + freeze) and returns a `Transaction`
  ready to enter the mempool. It does NOT touch balances — that
  happens at mining time via `apply_block_deltas` so the invariant
  "balance moves only when a block is mined" holds.
- `MintService.mint_to` is the ADMIN-only path that credits a wallet.
  It writes a coinbase transaction (signature = "MINT") into the
  mempool so the credit shows up as a regular history entry on the
  next block.

Design note on the canonical signing message: both `TransferService`
and the chain validator (`_validate_blocks`) call
`canonical_transfer_message(...)` from `domain/crypto`, so verification
is bit-identical between the entry-point check and the post-mining
re-validation.
"""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import NamedTuple

from domain.crypto import (
    canonical_transfer_message,
    derive_keypair,
    generate_mnemonic,
    mnemonic_to_seed,
    public_key_hex,
    verify,
)
from domain.models import Transaction
from domain.wallet_repository import (
    InsufficientBalanceError,
    NonceReplayError,
    WalletFrozenError,
    WalletNotFoundError,
    WalletRepositoryProtocol,
    WalletRecord,
)


# Sentinel used in place of a real ECDSA signature on coinbase / mint
# transactions. The chain validator skips signature verification when it
# sees this string and applies the credit unconditionally.
COINBASE_SIGNATURE = "MINT"


class CreatedWallet(NamedTuple):
    wallet_id: str
    public_key: str
    mnemonic: str
    """The 12-word BIP-39 phrase. Only ever returned from
    `WalletService.create_wallet` — never persisted."""


class WalletService:
    def __init__(self, wallets: WalletRepositoryProtocol) -> None:
        self._wallets = wallets

    def create_wallet(self, *, user_id: str) -> CreatedWallet:
        wallet_id = "w_" + secrets.token_hex(8)
        mnemonic = generate_mnemonic()
        seed = mnemonic_to_seed(mnemonic)
        _, public_key = derive_keypair(seed)
        pub_hex = public_key_hex(public_key)
        self._wallets.create_wallet(
            wallet_id=wallet_id,
            user_id=user_id,
            public_key=pub_hex,
        )
        # Wipe the seed/mnemonic locals as a mild defence-in-depth so
        # they do not survive a long-lived reference. (The mnemonic
        # itself still flows back to the caller in the return value.)
        del seed
        return CreatedWallet(wallet_id=wallet_id, public_key=pub_hex, mnemonic=mnemonic)

    def get_wallet(self, wallet_id: str) -> WalletRecord | None:
        return self._wallets.get_wallet(wallet_id)

    def list_user_wallets(self, user_id: str) -> list[WalletRecord]:
        return self._wallets.list_user_wallets(user_id)

    def set_frozen(self, *, wallet_id: str, frozen: bool) -> None:
        self._wallets.set_frozen(wallet_id=wallet_id, frozen=frozen)


class SignatureRejectedError(Exception):
    """Raised when an incoming transfer carries a signature that does
    not verify against the sender wallet's stored public key."""


class TransferService:
    """Validates and stages a signed transfer.

    Order of checks (least expensive first):
      1. Sender + receiver wallets exist
      2. Sender wallet is not frozen
      3. Sender has enough balance for the amount
      4. Nonce is strictly greater than `last_used_nonce`
      5. Signature verifies against `sender.public_key`

    On success the wallet's nonce is reserved (so a duplicate request
    inside a few milliseconds rejects on step 4) and a `Transaction`
    is returned for the caller to drop into the mempool.
    """

    def __init__(self, wallets: WalletRepositoryProtocol) -> None:
        self._wallets = wallets

    def build_transaction(
        self,
        *,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
        nonce: int,
        signature: str,
        sender_username: str,
        receiver_username: str,
    ) -> Transaction:
        sender = self._wallets.get_wallet(sender_wallet_id)
        receiver = self._wallets.get_wallet(receiver_wallet_id)
        if sender is None:
            raise WalletNotFoundError(sender_wallet_id)
        if receiver is None:
            raise WalletNotFoundError(receiver_wallet_id)
        if sender.frozen or receiver.frozen:
            raise WalletFrozenError(
                sender_wallet_id if sender.frozen else receiver_wallet_id
            )
        if sender.balance < amount:
            raise InsufficientBalanceError(sender_wallet_id)

        message = canonical_transfer_message(
            sender_wallet_id=sender_wallet_id,
            receiver_wallet_id=receiver_wallet_id,
            amount=amount,
            nonce=nonce,
        )
        if not verify(sender.public_key, signature, message):
            raise SignatureRejectedError(sender_wallet_id)

        # Reserve the nonce LAST so a signature-rejected attempt does
        # not poison the wallet's nonce sequence.
        self._wallets.reserve_nonce(wallet_id=sender_wallet_id, nonce=nonce)

        return Transaction(
            sender=sender_username,
            receiver=receiver_username,
            amount=amount,
            sender_wallet_id=sender_wallet_id,
            receiver_wallet_id=receiver_wallet_id,
            nonce=nonce,
            signature=signature,
        )


class MintService:
    """Coinbase mint — ADMIN-only entry point that credits a wallet
    without a real signature. Implemented as a transaction with
    `signature = COINBASE_SIGNATURE` so the chain serialises mint
    history alongside regular transfers."""

    def __init__(self, wallets: WalletRepositoryProtocol) -> None:
        self._wallets = wallets

    def build_mint(
        self,
        *,
        receiver_wallet_id: str,
        amount: Decimal,
        receiver_username: str,
        admin_username: str,
    ) -> Transaction:
        receiver = self._wallets.get_wallet(receiver_wallet_id)
        if receiver is None:
            raise WalletNotFoundError(receiver_wallet_id)
        if receiver.frozen:
            raise WalletFrozenError(receiver_wallet_id)
        if amount <= 0:
            raise ValueError("Mint amount must be positive")
        return Transaction(
            sender=f"COINBASE({admin_username})",
            receiver=receiver_username,
            amount=amount,
            sender_wallet_id="",
            receiver_wallet_id=receiver_wallet_id,
            nonce=0,
            signature=COINBASE_SIGNATURE,
        )


def apply_block_deltas(
    wallets: WalletRepositoryProtocol, transactions: list[Transaction]
) -> None:
    """Apply balance deltas for every transaction in a freshly mined
    block. Coinbase / mint txns credit the receiver; regular transfers
    debit sender + credit receiver atomically.

    Back-compat: legacy v0.10.0 transactions submitted through
    `POST /api/v1/transactions` carry empty `sender_wallet_id` /
    `receiver_wallet_id` and no signature. They flow through the
    mempool and land in blocks (so chain history records them) but do
    NOT move balances — there are no wallets to credit. Phase I.4's
    frontend will switch to `POST /api/v1/transactions/signed` and the
    legacy path will become deprecated.
    """
    for tx in transactions:
        if tx.signature == COINBASE_SIGNATURE:
            if tx.receiver_wallet_id:
                wallets.credit(wallet_id=tx.receiver_wallet_id, amount=tx.amount)
        elif tx.sender_wallet_id and tx.receiver_wallet_id:
            wallets.apply_transfer(
                sender_wallet_id=tx.sender_wallet_id,
                receiver_wallet_id=tx.receiver_wallet_id,
                amount=tx.amount,
            )
        # else: legacy transaction without wallet IDs — chain records it
        # for history but no balance change.
