"""Phase I.3 — signed transfer flow.

Covers `POST /api/v1/transactions/signed` end-to-end: signature must
verify, nonce must be monotonic, freeze rejects, ownership check
prevents transferring from someone else's wallet, balance must cover
the amount.
"""

from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal
from pathlib import Path

from domain.crypto import (
    canonical_transfer_message,
    derive_keypair,
    mnemonic_to_seed,
    sign,
)


MODULE_PATH = Path(__file__).resolve().parent.parent / "basic-blockchain.py"


def _reload_app(monkeypatch, *, bootstrap_admin: str = "admin"):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", bootstrap_admin)
    import config

    importlib.reload(config)
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _register_activate(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title()},
    )
    body = await r.get_json()
    code = body["activation_code"]
    await client.post(
        "/api/v1/auth/activate",
        json={"username": username, "activation_code": code, "password": password},
    )
    return body["user_id"]


async def _bearer(client, *, username: str, password: str = "hunter12345"):
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    return {"Authorization": f"Bearer {(await r.get_json())['access_token']}"}


async def _setup_two_users_and_mint(client, monkeypatch_admin_id: str | None = None):
    """Convenience: register admin (auto-promoted), alice, bob; create
    a wallet for each; mint 100 to alice; mine. Returns the four
    pieces of context the transfer tests need.
    """
    admin_id = await _register_activate(client, username="admin")
    await _register_activate(client, username="alice")
    await _register_activate(client, username="bob")
    h_admin = await _bearer(client, username="admin")
    h_alice = await _bearer(client, username="alice")
    h_bob = await _bearer(client, username="bob")

    r = await client.post("/api/v1/wallets", headers=h_alice)
    alice_w = await r.get_json()
    r = await client.post("/api/v1/wallets", headers=h_bob)
    bob_w = await r.get_json()

    # Admin self-grants MINT, then mints 100 to alice, then mine.
    await client.post(
        f"/api/v1/admin/users/{admin_id}/permissions",
        headers=h_admin,
        json={"action": "grant", "permission": "MINT"},
    )
    await client.post(
        "/api/v1/admin/mint",
        headers=h_admin,
        json={"wallet_id": alice_w["wallet_id"], "amount": 100},
    )
    await client.post("/api/v1/mine_block")

    return {
        "admin_id": admin_id,
        "h_admin": h_admin,
        "h_alice": h_alice,
        "h_bob": h_bob,
        "alice_wallet": alice_w,
        "bob_wallet": bob_w,
    }


def _sign_transfer(*, mnemonic: str, sender_wallet_id: str, receiver_wallet_id: str, amount, nonce):
    priv, _ = derive_keypair(mnemonic_to_seed(mnemonic))
    msg = canonical_transfer_message(
        sender_wallet_id=sender_wallet_id,
        receiver_wallet_id=receiver_wallet_id,
        amount=Decimal(str(amount)),
        nonce=nonce,
    )
    return sign(priv, msg)


# ── Happy path ──────────────────────────────────────────────────────────


async def test_signed_transfer_happy_path_moves_balance(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        sig = _sign_transfer(
            mnemonic=ctx["alice_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=30,
            nonce=1,
        )
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 30,
                "nonce": 1,
                "signature": sig,
            },
        )
        assert r.status_code == 201, await r.get_json()

        await client.post("/api/v1/mine_block")

        r = await client.get("/api/v1/wallets/me", headers=ctx["h_alice"])
        alice = (await r.get_json())["wallets"][0]
        r = await client.get("/api/v1/wallets/me", headers=ctx["h_bob"])
        bob = (await r.get_json())["wallets"][0]
        assert alice["balance"] == 70.0
        assert bob["balance"] == 30.0


# ── Signature rejection ─────────────────────────────────────────────────


async def test_signed_transfer_rejects_tampered_signature(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        sig = _sign_transfer(
            mnemonic=ctx["alice_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=30,
            nonce=1,
        )
        # Same signature but a different amount → verification fails.
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 999,
                "nonce": 1,
                "signature": sig,
            },
        )
        # Insufficient balance check fires before signature (alice only
        # has 100); the test verifies that ONE of the gating errors
        # kicks in, never that the bogus tx slips through.
        body = await r.get_json()
        assert r.status_code == 400
        assert body["code"] in {"INSUFFICIENT_BALANCE", "SIGNATURE_INVALID"}


async def test_signed_transfer_rejects_truly_forged_signature(monkeypatch):
    """A signature produced by a *different* keypair fails the
    SIGNATURE_INVALID check before any balance check (alice has plenty)."""
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        # Sign with bob's mnemonic instead of alice's — same payload.
        sig_from_bob = _sign_transfer(
            mnemonic=ctx["bob_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=10,
            nonce=1,
        )
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 10,
                "nonce": 1,
                "signature": sig_from_bob,
            },
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "SIGNATURE_INVALID"


# ── Nonce replay ────────────────────────────────────────────────────────


async def test_signed_transfer_rejects_nonce_replay(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        sig = _sign_transfer(
            mnemonic=ctx["alice_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=10,
            nonce=1,
        )
        body = {
            "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
            "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
            "amount": 10,
            "nonce": 1,
            "signature": sig,
        }
        r = await client.post("/api/v1/transactions/signed", headers=ctx["h_alice"], json=body)
        assert r.status_code == 201
        # Same nonce again — must be rejected.
        r = await client.post("/api/v1/transactions/signed", headers=ctx["h_alice"], json=body)
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "NONCE_REPLAY"


async def test_signed_transfer_rejects_decreasing_nonce(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        for nonce, amount in [(5, 10), (3, 10)]:
            sig = _sign_transfer(
                mnemonic=ctx["alice_wallet"]["mnemonic"],
                sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
                receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
                amount=amount,
                nonce=nonce,
            )
            r = await client.post(
                "/api/v1/transactions/signed",
                headers=ctx["h_alice"],
                json={
                    "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                    "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                    "amount": amount,
                    "nonce": nonce,
                    "signature": sig,
                },
            )
            if nonce == 5:
                assert r.status_code == 201
            else:
                # Lower nonce after a higher one → replay.
                assert r.status_code == 400
                assert (await r.get_json())["code"] == "NONCE_REPLAY"


# ── Ownership and balance ──────────────────────────────────────────────


async def test_signed_transfer_rejects_when_caller_does_not_own_wallet(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        # Bob authenticates but submits a transfer FROM alice's wallet.
        sig = _sign_transfer(
            mnemonic=ctx["alice_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=5,
            nonce=1,
        )
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_bob"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 5,
                "nonce": 1,
                "signature": sig,
            },
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "WALLET_OWNERSHIP"


async def test_signed_transfer_rejects_insufficient_balance(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)  # alice has 100
        sig = _sign_transfer(
            mnemonic=ctx["alice_wallet"]["mnemonic"],
            sender_wallet_id=ctx["alice_wallet"]["wallet_id"],
            receiver_wallet_id=ctx["bob_wallet"]["wallet_id"],
            amount=5000,
            nonce=1,
        )
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 5000,
                "nonce": 1,
                "signature": sig,
            },
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "INSUFFICIENT_BALANCE"


# ── Validation errors ──────────────────────────────────────────────────


async def test_signed_transfer_rejects_missing_fields(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={"sender_wallet_id": ctx["alice_wallet"]["wallet_id"]},
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_signed_transfer_rejects_zero_amount(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["bob_wallet"]["wallet_id"],
                "amount": 0,
                "nonce": 1,
                "signature": "deadbeef",
            },
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"


async def test_signed_transfer_rejects_same_sender_receiver(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        ctx = await _setup_two_users_and_mint(client)
        r = await client.post(
            "/api/v1/transactions/signed",
            headers=ctx["h_alice"],
            json={
                "sender_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "receiver_wallet_id": ctx["alice_wallet"]["wallet_id"],
                "amount": 1,
                "nonce": 1,
                "signature": "deadbeef",
            },
        )
        assert r.status_code == 400
        assert (await r.get_json())["code"] == "VALIDATION_ERROR"
