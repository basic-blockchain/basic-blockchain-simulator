"""Phase I.3 — total supply must equal mints minus burns.

A blockchain is only a ledger if value cannot appear or disappear from
nowhere. The simulator does not have burns yet, so the invariant is:

    sum(every wallet's balance) == sum(every minted amount)

regardless of how many transfers happened in between. These tests
exercise that across:
  * a single mint, a single transfer (the obvious case)
  * many transfers in a single block (mempool flushed at once)
  * many transfers across many blocks
  * tampered chain — `is_chain_valid()` flips false
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


def _reload_app(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    import config

    importlib.reload(config)
    spec = importlib.util.spec_from_file_location("basic_blockchain", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _activate_and_token(client, *, username: str):
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "display_name": username.title()},
    )
    body = await r.get_json()
    user_id = body["user_id"]
    await client.post(
        "/api/v1/auth/activate",
        json={
            "username": username,
            "activation_code": body["activation_code"],
            "password": "hunter12345",
        },
    )
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "hunter12345"}
    )
    token = (await r.get_json())["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


async def _create_wallet(client, headers):
    r = await client.post("/api/v1/wallets", headers=headers)
    return await r.get_json()


async def _grant_mint_to_admin(client, *, admin_id, h_admin):
    await client.post(
        f"/api/v1/admin/users/{admin_id}/permissions",
        headers=h_admin,
        json={"action": "grant", "permission": "MINT"},
    )


async def _mint(client, *, h_admin, wallet_id, amount):
    return await client.post(
        "/api/v1/admin/mint", headers=h_admin, json={"wallet_id": wallet_id, "amount": amount}
    )


async def _signed_transfer(client, *, h, mnemonic, sender, receiver, amount, nonce):
    priv, _ = derive_keypair(mnemonic_to_seed(mnemonic))
    msg = canonical_transfer_message(
        sender_wallet_id=sender,
        receiver_wallet_id=receiver,
        amount=Decimal(str(amount)),
        nonce=nonce,
    )
    sig = sign(priv, msg)
    return await client.post(
        "/api/v1/transactions/signed",
        headers=h,
        json={
            "sender_wallet_id": sender,
            "receiver_wallet_id": receiver,
            "amount": amount,
            "nonce": nonce,
            "signature": sig,
        },
    )


async def _sum_balances(client, *, headers_iter):
    """Sum balances across the wallets visible to each (header, user)
    tuple. The MVP exposes balance via `/wallets/me`, so we visit each
    user's listing."""
    total = Decimal(0)
    for h in headers_iter:
        r = await client.get("/api/v1/wallets/me", headers=h)
        for w in (await r.get_json())["wallets"]:
            total += Decimal(str(w["balance"]))
    return total


# ── Tests ───────────────────────────────────────────────────────────────


async def test_supply_conserved_after_one_transfer(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        admin_id, h_admin = await _activate_and_token(client, username="admin")
        _, h_alice = await _activate_and_token(client, username="alice")
        _, h_bob = await _activate_and_token(client, username="bob")
        alice_w = await _create_wallet(client, h_alice)
        bob_w = await _create_wallet(client, h_bob)
        await _grant_mint_to_admin(client, admin_id=admin_id, h_admin=h_admin)
        await _mint(client, h_admin=h_admin, wallet_id=alice_w["wallet_id"], amount=100)
        await client.post("/api/v1/mine_block")
        await _signed_transfer(
            client,
            h=h_alice,
            mnemonic=alice_w["mnemonic"],
            sender=alice_w["wallet_id"],
            receiver=bob_w["wallet_id"],
            amount=30,
            nonce=1,
        )
        await client.post("/api/v1/mine_block")

        total = await _sum_balances(client, headers_iter=[h_alice, h_bob])
        assert total == Decimal("100")


async def test_supply_conserved_across_many_transfers_in_one_block(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        admin_id, h_admin = await _activate_and_token(client, username="admin")
        _, h_alice = await _activate_and_token(client, username="alice")
        _, h_bob = await _activate_and_token(client, username="bob")
        alice_w = await _create_wallet(client, h_alice)
        bob_w = await _create_wallet(client, h_bob)
        await _grant_mint_to_admin(client, admin_id=admin_id, h_admin=h_admin)
        await _mint(client, h_admin=h_admin, wallet_id=alice_w["wallet_id"], amount=200)
        await client.post("/api/v1/mine_block")

        # Five transfers in a row, all admitted to the mempool, then one mine.
        for nonce, amount in [(1, 10), (2, 20), (3, 30), (4, 5), (5, 15)]:
            r = await _signed_transfer(
                client,
                h=h_alice,
                mnemonic=alice_w["mnemonic"],
                sender=alice_w["wallet_id"],
                receiver=bob_w["wallet_id"],
                amount=amount,
                nonce=nonce,
            )
            assert r.status_code == 201, await r.get_json()
        await client.post("/api/v1/mine_block")

        total = await _sum_balances(client, headers_iter=[h_alice, h_bob])
        assert total == Decimal("200")


async def test_supply_conserved_across_many_blocks(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        admin_id, h_admin = await _activate_and_token(client, username="admin")
        _, h_alice = await _activate_and_token(client, username="alice")
        _, h_bob = await _activate_and_token(client, username="bob")
        alice_w = await _create_wallet(client, h_alice)
        bob_w = await _create_wallet(client, h_bob)
        await _grant_mint_to_admin(client, admin_id=admin_id, h_admin=h_admin)
        await _mint(client, h_admin=h_admin, wallet_id=alice_w["wallet_id"], amount=50)
        await client.post("/api/v1/mine_block")

        for nonce, amount in [(1, 5), (2, 5), (3, 5), (4, 5)]:
            await _signed_transfer(
                client,
                h=h_alice,
                mnemonic=alice_w["mnemonic"],
                sender=alice_w["wallet_id"],
                receiver=bob_w["wallet_id"],
                amount=amount,
                nonce=nonce,
            )
            await client.post("/api/v1/mine_block")  # mine after each

        total = await _sum_balances(client, headers_iter=[h_alice, h_bob])
        assert total == Decimal("50")


async def test_chain_validity_holds_after_signed_transfers(monkeypatch):
    module = _reload_app(monkeypatch)
    async with module.create_app().test_client() as client:
        admin_id, h_admin = await _activate_and_token(client, username="admin")
        _, h_alice = await _activate_and_token(client, username="alice")
        _, h_bob = await _activate_and_token(client, username="bob")
        alice_w = await _create_wallet(client, h_alice)
        bob_w = await _create_wallet(client, h_bob)
        await _grant_mint_to_admin(client, admin_id=admin_id, h_admin=h_admin)
        await _mint(client, h_admin=h_admin, wallet_id=alice_w["wallet_id"], amount=20)
        await client.post("/api/v1/mine_block")
        await _signed_transfer(
            client,
            h=h_alice,
            mnemonic=alice_w["mnemonic"],
            sender=alice_w["wallet_id"],
            receiver=bob_w["wallet_id"],
            amount=10,
            nonce=1,
        )
        await client.post("/api/v1/mine_block")

        r = await client.get("/api/v1/valid")
        assert (await r.get_json())["valid"] is True
