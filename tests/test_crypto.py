"""Phase I.3 — domain/crypto.py round-trips and tamper detection."""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.crypto import (
    canonical_transfer_message,
    derive_keypair,
    generate_mnemonic,
    mnemonic_to_seed,
    public_key_from_hex,
    public_key_hex,
    sign,
    verify,
)


def test_generate_mnemonic_returns_12_words():
    m = generate_mnemonic()
    assert len(m.split()) == 12


def test_mnemonic_to_seed_is_deterministic():
    m = generate_mnemonic()
    assert mnemonic_to_seed(m) == mnemonic_to_seed(m)


def test_mnemonic_to_seed_rejects_invalid_phrase():
    with pytest.raises(ValueError):
        mnemonic_to_seed("not actually a valid bip39 mnemonic phrase here")


def test_derive_keypair_is_deterministic():
    m = generate_mnemonic()
    seed = mnemonic_to_seed(m)
    priv1, pub1 = derive_keypair(seed)
    priv2, pub2 = derive_keypair(seed)
    assert public_key_hex(pub1) == public_key_hex(pub2)


def test_public_key_hex_round_trip():
    seed = mnemonic_to_seed(generate_mnemonic())
    _, pub = derive_keypair(seed)
    h = public_key_hex(pub)
    assert len(h) == 66  # 33 bytes compressed
    pub_back = public_key_from_hex(h)
    assert public_key_hex(pub_back) == h


def test_canonical_transfer_message_format():
    msg = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("5"), nonce=3
    )
    assert msg == b"w1:w2:5:3"


def test_canonical_transfer_message_keeps_decimals_plain():
    # Numbers like 1.50 must serialise as "1.5" — no exponents — so the
    # signed bytes match what the frontend will produce.
    msg = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("1.50"), nonce=1
    )
    assert msg == b"w1:w2:1.5:1"


def test_sign_and_verify_round_trip():
    seed = mnemonic_to_seed(generate_mnemonic())
    priv, pub = derive_keypair(seed)
    msg = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("10"), nonce=1
    )
    sig = sign(priv, msg)
    assert verify(public_key_hex(pub), sig, msg) is True


def test_verify_rejects_tampered_amount():
    seed = mnemonic_to_seed(generate_mnemonic())
    priv, pub = derive_keypair(seed)
    msg = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("10"), nonce=1
    )
    sig = sign(priv, msg)
    tampered = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("999"), nonce=1
    )
    assert verify(public_key_hex(pub), sig, tampered) is False


def test_verify_rejects_signature_from_a_different_key():
    seed_a = mnemonic_to_seed(generate_mnemonic())
    seed_b = mnemonic_to_seed(generate_mnemonic())
    priv_a, _ = derive_keypair(seed_a)
    _, pub_b = derive_keypair(seed_b)
    msg = canonical_transfer_message(
        sender_wallet_id="w1", receiver_wallet_id="w2", amount=Decimal("1"), nonce=1
    )
    sig_a = sign(priv_a, msg)
    # Bob's public key should not validate Alice's signature.
    assert verify(public_key_hex(pub_b), sig_a, msg) is False


def test_verify_returns_false_on_malformed_inputs():
    assert verify("not-hex", "also-not-hex", b"msg") is False
    assert verify("00" * 33, "00" * 64, b"msg") is False  # syntactically OK, semantically wrong
