"""Wallet cryptography (Phase I.3).

BIP-39 mnemonic generation, secp256k1 keypair derivation, and ECDSA
sign / verify for transfer authorisation. Pure domain — no Quart, no
psycopg2 imports — so tests can exercise the full crypto path without a
database.

The mnemonic returned by `generate_mnemonic()` is a 12-word BIP-39
phrase. `mnemonic_to_seed()` runs the standard PBKDF2 to produce a
512-bit seed. `derive_keypair(seed)` takes the first 32 bytes of that
seed and uses them as the private scalar — this is intentionally simpler
than full BIP-32 HD derivation: every wallet uses ONE keypair, derived
deterministically from its mnemonic. Phase J / a future phase can
upgrade to BIP-32 paths so a single mnemonic backs many wallets without
breaking the on-chain contract.

The canonical signing payload for a transfer is the UTF-8 bytes of:

    f"{sender_wallet_id}:{receiver_wallet_id}:{amount}:{nonce}"

where `amount` is the decimal repr of `Decimal(amount)` (no exponent).
Both signer and verifier MUST use exactly that string — see
`canonical_transfer_message()` for the single source of truth.
"""

from __future__ import annotations

from decimal import Decimal

from coincurve import PrivateKey, PublicKey
from mnemonic import Mnemonic


# 128 bits of entropy → 12-word BIP-39 phrase (the most common length;
# secure enough for the simulator and easy for users to write down).
_MNEMONIC_STRENGTH_BITS = 128

# Seed phrase passphrase (BIP-39 optional second factor). The simulator
# does not expose it on the API; it is fixed to the empty string so the
# same mnemonic always produces the same seed. A future phase can lift
# this restriction once the UI has a way to ask for it without leaking.
_BIP39_PASSPHRASE = ""


def generate_mnemonic() -> str:
    """Return a fresh 12-word BIP-39 mnemonic."""
    return Mnemonic("english").generate(strength=_MNEMONIC_STRENGTH_BITS)


def mnemonic_to_seed(mnemonic: str) -> bytes:
    """Return the 64-byte BIP-39 seed for a mnemonic. Raises if the
    mnemonic checksum is invalid."""
    if not Mnemonic("english").check(mnemonic):
        raise ValueError("Invalid BIP-39 mnemonic")
    return Mnemonic.to_seed(mnemonic, passphrase=_BIP39_PASSPHRASE)


def derive_keypair(seed: bytes) -> tuple[PrivateKey, PublicKey]:
    """Derive a secp256k1 keypair from a BIP-39 seed.

    Uses the first 32 bytes of the 64-byte BIP-39 seed as the private
    scalar. Deterministic: same mnemonic → same keypair every time.
    """
    if len(seed) < 32:
        raise ValueError("Seed must be at least 32 bytes")
    priv = PrivateKey(seed[:32])
    return priv, priv.public_key


def public_key_hex(public_key: PublicKey) -> str:
    """Serialise a public key as 33-byte compressed hex — the format
    persisted in `wallets.public_key`."""
    return public_key.format(compressed=True).hex()


def public_key_from_hex(hex_str: str) -> PublicKey:
    """Inverse of `public_key_hex`."""
    return PublicKey(bytes.fromhex(hex_str))


def canonical_transfer_message(
    *,
    sender_wallet_id: str,
    receiver_wallet_id: str,
    amount: Decimal,
    nonce: int,
) -> bytes:
    """The single source of truth for the bytes signed by a transfer.

    Both the client (browser, when it has the mnemonic) and the server
    (when verifying) must produce exactly this message. The encoding is
    deliberately simple — colon-separated UTF-8 — so a misimplemented
    client cannot accidentally produce a different valid signature.

    The amount uses `Decimal.__str__()` (no exponent) so 1 == 1, not
    1E0; the nonce is a plain integer.
    """
    parts = [sender_wallet_id, receiver_wallet_id, _format_amount(amount), str(nonce)]
    return ":".join(parts).encode("utf-8")


def _format_amount(amount: Decimal) -> str:
    """Format `Decimal` without scientific notation. Matches what the
    frontend would produce from `amount.toString()` after constructing
    a `Decimal`."""
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    # `normalize()` strips trailing zeros, but for amounts like 100 it
    # produces 1E+2 — undo that with a manual format if needed.
    plain = format(amount, "f").rstrip("0").rstrip(".") if "." in format(amount, "f") else format(amount, "f")
    return plain or "0"


def sign(private_key: PrivateKey, message: bytes) -> str:
    """Return a hex-encoded ECDSA signature."""
    return private_key.sign(message).hex()


def verify(public_key_hex_str: str, signature_hex: str, message: bytes) -> bool:
    """Verify a hex signature against a hex public key. False on any
    failure (bad encoding, wrong key, tampered message)."""
    try:
        pub = public_key_from_hex(public_key_hex_str)
        return pub.verify(bytes.fromhex(signature_hex), message)
    except (ValueError, Exception):  # noqa: BLE001 — coincurve raises a base Exception
        return False
