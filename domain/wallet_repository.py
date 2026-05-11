"""Wallet persistence contract + an in-memory implementation for tests.

The PostgreSQL implementation lives in
`infrastructure/postgres_wallet_store.py`. The HTTP layer talks to
whichever adapter the app factory injects.

The contract here is intentionally narrow: CRUD for wallets, nonce
gating, balance mutation, and the read paths the API needs. The actual
business logic (mnemonic generation, signature verification, supply
conservation) lives in `domain/wallet.py` so this protocol stays a pure
storage contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import Protocol


@dataclass(slots=True)
class WalletRecord:
    wallet_id: str
    user_id: str
    currency: str
    wallet_type: str
    balance: Decimal
    public_key: str
    frozen: bool


@dataclass(slots=True)
class WalletAdminRecord:
    """Wallet row enriched with owner metadata.

    Returned by `list_all_wallets`, the admin-only endpoint that joins
    wallets with their owner so the UI can render "wallet -> who owns it"
    without making N follow-up requests.
    """

    wallet_id: str
    user_id: str
    username: str
    display_name: str
    currency: str
    wallet_type: str
    balance: Decimal
    public_key: str
    frozen: bool


class WalletType(str, Enum):
    USER = "USER"
    TREASURY = "TREASURY"
    FEE = "FEE"


class WalletNotFoundError(Exception):
    """Raised when a wallet ID does not exist."""


class WalletFrozenError(Exception):
    """Raised when a transfer is attempted on a frozen wallet."""


class NonceReplayError(Exception):
    """Raised when an incoming transfer's nonce is not strictly greater
    than the wallet's last_used_nonce."""


class InsufficientBalanceError(Exception):
    """Raised when a transfer / mint exceeds the sender's balance."""


class CurrencyMismatchError(Exception):
    """Raised when a transfer is attempted across different currencies."""


class WalletRepositoryProtocol(Protocol):
    def create_wallet(
        self,
        *,
        wallet_id: str,
        user_id: str,
        public_key: str,
        currency: str = "NATIVE",
        wallet_type: str = WalletType.USER.value,
    ) -> None: ...

    def get_wallet(self, wallet_id: str) -> WalletRecord | None: ...

    def list_user_wallets(self, user_id: str) -> list[WalletRecord]: ...

    def set_frozen(self, *, wallet_id: str, frozen: bool) -> None: ...

    def reserve_nonce(self, *, wallet_id: str, nonce: int) -> None:
        """Atomically check-and-update wallet_nonces.

        Raises `NonceReplayError` if `nonce <= last_used_nonce`. On
        success the row is upserted with `last_used_nonce = nonce`.
        """
        ...

    def apply_transfer(
        self,
        *,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
    ) -> None:
        """Debit sender, credit receiver in one transaction. Raises
        `InsufficientBalanceError` / `WalletFrozenError` on guard
        failures."""
        ...

    def credit(self, *, wallet_id: str, amount: Decimal) -> None:
        """Increase a wallet's balance (used by mint / coinbase)."""
        ...

    def total_supply(self) -> Decimal:
        """Sum of every wallet's balance — invariant for the
        conservation-of-supply test."""
        ...

    def list_all_wallets(self) -> list[WalletAdminRecord]:
        """Return every wallet enriched with its owner's username and
        display_name. Used by the admin `/admin/wallets` endpoint."""
        ...

    def find_wallet_by_type_currency(
        self,
        *,
        wallet_type: str,
        currency: str,
    ) -> WalletRecord | None: ...


class InMemoryWalletStore:
    """Wallet store that lives in process memory. Used by unit tests so
    the wallet/transfer flow can be exercised without a database."""

    def __init__(self) -> None:
        self._wallets: dict[str, WalletRecord] = {}
        self._nonces: dict[str, int] = {}

    def create_wallet(
        self,
        *,
        wallet_id: str,
        user_id: str,
        public_key: str,
        currency: str = "NATIVE",
        wallet_type: str = WalletType.USER.value,
    ) -> None:
        if wallet_id in self._wallets:
            raise ValueError(f"Wallet {wallet_id} already exists")
        self._wallets[wallet_id] = WalletRecord(
            wallet_id=wallet_id,
            user_id=user_id,
            currency=currency,
            wallet_type=wallet_type,
            balance=Decimal(0),
            public_key=public_key,
            frozen=False,
        )

    def get_wallet(self, wallet_id: str) -> WalletRecord | None:
        return self._wallets.get(wallet_id)

    def list_user_wallets(self, user_id: str) -> list[WalletRecord]:
        return [w for w in self._wallets.values() if w.user_id == user_id]

    def set_frozen(self, *, wallet_id: str, frozen: bool) -> None:
        w = self._wallets.get(wallet_id)
        if w is None:
            raise WalletNotFoundError(wallet_id)
        self._wallets[wallet_id] = WalletRecord(
            wallet_id=w.wallet_id,
            user_id=w.user_id,
            currency=w.currency,
            wallet_type=w.wallet_type,
            balance=w.balance,
            public_key=w.public_key,
            frozen=frozen,
        )

    def reserve_nonce(self, *, wallet_id: str, nonce: int) -> None:
        last = self._nonces.get(wallet_id, 0)
        if nonce <= last:
            raise NonceReplayError(
                f"nonce {nonce} <= last used {last} for wallet {wallet_id}"
            )
        self._nonces[wallet_id] = nonce

    def apply_transfer(
        self,
        *,
        sender_wallet_id: str,
        receiver_wallet_id: str,
        amount: Decimal,
    ) -> None:
        sender = self._wallets.get(sender_wallet_id)
        receiver = self._wallets.get(receiver_wallet_id)
        if sender is None:
            raise WalletNotFoundError(sender_wallet_id)
        if receiver is None:
            raise WalletNotFoundError(receiver_wallet_id)
        if sender.frozen:
            raise WalletFrozenError(sender_wallet_id)
        if receiver.frozen:
            raise WalletFrozenError(receiver_wallet_id)
        if sender.balance < amount:
            raise InsufficientBalanceError(sender_wallet_id)
        # Replace records with new balances (frozen-state preserving).
        self._wallets[sender_wallet_id] = WalletRecord(
            wallet_id=sender.wallet_id,
            user_id=sender.user_id,
            currency=sender.currency,
            wallet_type=sender.wallet_type,
            balance=sender.balance - amount,
            public_key=sender.public_key,
            frozen=sender.frozen,
        )
        self._wallets[receiver_wallet_id] = WalletRecord(
            wallet_id=receiver.wallet_id,
            user_id=receiver.user_id,
            currency=receiver.currency,
            wallet_type=receiver.wallet_type,
            balance=receiver.balance + amount,
            public_key=receiver.public_key,
            frozen=receiver.frozen,
        )

    def credit(self, *, wallet_id: str, amount: Decimal) -> None:
        w = self._wallets.get(wallet_id)
        if w is None:
            raise WalletNotFoundError(wallet_id)
        if w.frozen:
            raise WalletFrozenError(wallet_id)
        self._wallets[wallet_id] = WalletRecord(
            wallet_id=w.wallet_id,
            user_id=w.user_id,
            currency=w.currency,
            wallet_type=w.wallet_type,
            balance=w.balance + amount,
            public_key=w.public_key,
            frozen=w.frozen,
        )

    def total_supply(self) -> Decimal:
        return sum((w.balance for w in self._wallets.values()), Decimal(0))

    def list_all_wallets(self) -> list[WalletAdminRecord]:
        # InMemory has no users table to join against, so username and
        # display_name come back empty. Tests that need richer data wire
        # the Postgres store via the integration suite.
        return [
            WalletAdminRecord(
                wallet_id=w.wallet_id,
                user_id=w.user_id,
                username="",
                display_name="",
                currency=w.currency,
                wallet_type=w.wallet_type,
                balance=w.balance,
                public_key=w.public_key,
                frozen=w.frozen,
            )
            for w in self._wallets.values()
        ]

    def find_wallet_by_type_currency(
        self,
        *,
        wallet_type: str,
        currency: str,
    ) -> WalletRecord | None:
        for w in self._wallets.values():
            if w.wallet_type == wallet_type and w.currency == currency:
                return w
        return None
