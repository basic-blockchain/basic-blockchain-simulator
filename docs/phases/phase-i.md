# Phase I — Multi-user Wallet MVP (JWT auth + RBAC + wallets)

**Status:** active sprint — kicked off 2026-05-07.
**Tracks:** simulator (this repo) + frontend (`basic-blockchain-frontend`).
**Releases:** simulator v0.11.0 → v0.12.0 → v0.13.0 (one per sub-phase); frontend v0.7.0 (single release at the end).

## Goal

Convert the simulator from a free-for-all transaction endpoint into a usable wallet backend: real users with login, JWT identity, role-based authorisation, per-user wallets with balances, and transferences validated by short-lived per-wallet tokens with replay protection.

## Decisions (locked, user-approved 2026-05-07)

| # | Decision | Outcome |
|---|----------|---------|
| 1 | Tenancy | **Multi-user, single-org.** One shared chain, many users with their wallets. No `tenant_id` column anywhere. |
| 2 | Transfer authorisation | **JWT for identity + ECDSA secp256k1 signature derived from a BIP-39 mnemonic, with monotonic nonce.** Each wallet is created with a 12-word BIP-39 mnemonic; the private key is derived from it and never persisted server-side. The server stores only `wallets.public_key`. Each transfer carries a signature over `(sender_wallet_id, receiver_wallet_id, amount, nonce)` that the server verifies against the sender's public key. Replay is prevented by the `wallet_nonces` table. **Replaces** the short-lived `auth_token` from `blockchain-data-model` with a Web3-standard pattern (MetaMask-style). |
| 3 | Currency model | **Phased.** Phase I uses a single hard-coded native coin. Phase J introduces `currencies` and per-(wallet, currency) balances. Phase J is documented as a follow-up at the bottom of this doc and is **not in scope** for I.x. |
| 4 | Persistence | Postgres remains source of truth. New tables (`users`, `user_credentials`, `user_roles`, `wallets`, `wallet_nonces`, `audit_log`, `role_permissions`, `user_permissions`) live next to `blocks`/`transactions`/`mempool`/`nodes`. The mnemonic itself is **never** persisted; only `wallets.public_key`. |
| 5 | P2P | Unchanged. Peers share *the* chain. Phase I just adds signature + nonce validation on top of the existing propagation surface. SaaS-style isolation is out of scope. |
| 6 | Bootstrap ADMIN | First registered user with `username == BOOTSTRAP_ADMIN_USERNAME` (env var) is auto-promoted to ADMIN. Otherwise the user gets the default role (VIEWER). Documented in startup logs. |
| 7 | Mnemonic generation locality | **Phase I.3 (MVP):** server generates the mnemonic, derives the keypair, returns `{wallet_id, public_key, mnemonic}` ONCE in the wallet creation response. The mnemonic lives in memory for the duration of that request and is then discarded; it is not logged or persisted. The frontend is responsible for showing it and forcing the user to confirm they saved it. **Phase J+ (future):** move generation to 100% client-side (`@scure/bip39` + `@noble/curves`); server only ever sees the public key. Documented as deferred. |

## Reference repo

`c:\Users\User\Documents\sapir\blockchain_usb\scripts\python\blockchain-data-model` already implements the full pattern (PyJWT HS256, bcrypt, RBAC with 3-level overrides, wallet auth_token + nonce, audit log). This phase **adapts** those patterns to the simulator's HTTP/Quart architecture.

## Sub-phases and work-item tables

### Phase I.1 — Authentication foundation (sim **v0.11.0**)

Branch: `feat/auth-foundation`.

| ID | Work item | Files |
|----|-----------|-------|
| I.1.1 | Plan doc, dependencies, config (`PyJWT`, `bcrypt`; new env vars: `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_TTL_SECONDS`, `BCRYPT_ROUNDS`, `BOOTSTRAP_ADMIN_USERNAME`) | `docs/phases/phase-i.md`, `requirements.txt`, `requirements-dev.txt`, `config.py` |
| I.1.2 | V007 `users`, V008 `user_credentials` + `user_roles` (mirrors blockchain-data-model V001/V002) | `migrations/versions/V007__users.sql`, `V008__user_credentials.sql` |
| I.1.3 | `domain/auth.py`: `Role` enum, `hash_password`/`verify_password`, `create_jwt`/`decode_jwt`, activation-code helper | `domain/auth.py` |
| I.1.4 | `domain/user_repository.py` Protocol + `infrastructure/postgres_user_store.py` (CRUD users + credentials + roles) | `domain/user_repository.py`, `infrastructure/postgres_user_store.py` |
| I.1.5 | `api/auth_middleware.py`: Quart `before_request` hook that decodes Bearer tokens and attaches `g.current_user` (None on public routes) | `api/auth_middleware.py` |
| I.1.6 | `api/auth_routes.py`: `POST /auth/register` (with bootstrap-admin), `POST /auth/activate`, `POST /auth/login`, `GET /auth/me` | `api/auth_routes.py`, `basic-blockchain.py` |
| I.1.7 | Unit + HTTP tests: hash, JWT round-trip, register→activate→login→me happy path, 401 on tampered/expired JWT | `tests/test_auth.py` |
| I.1.8 | Docs: api-reference auth section, data-model users/credentials, business-rules BR-AU-01..N, releases/v0.11.0.md | docs |

**Acceptance:** unit suite green ≥80% coverage; full register→activate→login→me flow works against the real PG schema; tampered or expired JWT returns 401 with `code: AUTH_INVALID_TOKEN` or `AUTH_EXPIRED_TOKEN`; `GET /chain` and `GET /valid` remain public.

### Phase I.2 — Roles & permissions / RBAC (sim **v0.12.0**)

Branch: `feat/rbac`.

| ID | Work item | Files |
|----|-----------|-------|
| I.2.1 | V009 `role_permissions` + `user_permissions` (mirrors blockchain-data-model V006) | `migrations/versions/V009__roles_permissions.sql` |
| I.2.2 | `domain/permissions.py`: `Permission` enum, `ROLE_PERMISSIONS` defaults, `has_permission(roles, perm, role_overrides, user_permissions, user_id)` | `domain/permissions.py` |
| I.2.3 | `@require_permission(Permission.X)` decorator that aborts with 403 + `code: FORBIDDEN` | `api/permissions.py` |
| I.2.4 | Admin endpoints: `GET /admin/users`, `POST /admin/users/<id>/roles`, `POST /admin/users/<id>/ban`/`unban` | `api/admin_routes.py` |
| I.2.5 | V010 `audit_log` (`actor_id`, `action`, `target_id`, `details JSONB`, `created_at`) and `domain/audit.py` writer | `migrations/versions/V010__audit_log.sql`, `domain/audit.py` |
| I.2.6 | Tests: ADMIN allowed paths, OPERATOR denied paths, role override, user override, audit row written | `tests/test_rbac.py` |
| I.2.7 | Docs: BR-RB-01..N in business-rules, releases/v0.12.0.md | docs |

**Acceptance:** non-ADMIN gets 403 on admin endpoints; permission overrides exercised; every admin action writes an `audit_log` row.

### Phase I.3 — Wallets, balances, signed transfers (sim **v0.13.0**)

Branch: `feat/wallets-and-transfers`.

Phase I.3 implements the Web3 wallet pattern: each wallet owns a secp256k1 keypair derived from a BIP-39 mnemonic. The private key **never lives in the database**; it is derived in-memory at creation, the mnemonic is returned once, then discarded. Each transfer carries an ECDSA signature the server verifies against the wallet's stored public key.

**New backend dependencies:** `mnemonic>=0.21` (BIP-39 wordlists + checksum), `coincurve>=20.0` (libsecp256k1 binding for ECDSA + key derivation).

**New frontend dependencies (Phase I.4):** `@scure/bip39`, `@noble/curves`.

| ID | Work item | Files |
|----|-----------|-------|
| I.3.1 | V011 `wallets` (id, user_id FK, currency='NATIVE', balance, **`public_key TEXT NOT NULL`**, frozen) + V012 `wallet_nonces` (wallet_id PK, last_used_nonce, last_used_at). **Truncates `mempool` and `transactions`** because the new tx shape adds wallet IDs + signature. | `migrations/versions/V011__wallets.sql`, `V012__wallet_nonces.sql` |
| I.3.2 | `domain/crypto.py`: `generate_mnemonic()`, `mnemonic_to_seed()`, `derive_keypair(seed)` → `(priv, pub)`, `sign(priv, message)`, `verify(pub, sig, message)`. Canonical signing message: `f"{sender_wallet_id}:{receiver_wallet_id}:{amount_str}:{nonce}"`. | `domain/crypto.py` |
| I.3.3 | `domain/models.py` Transaction: add `sender_wallet_id`, `receiver_wallet_id`, `nonce: int`, `signature: str` (hex). Keep `sender`/`receiver` display strings for v0.6.0 frontend back-compat (resolved to username). `to_dict()` emits all fields. | `domain/models.py` |
| I.3.4 | `domain/wallet.py`: `WalletService.create_wallet` generates the mnemonic, derives the keypair, persists ONLY the public key, returns `{wallet_id, public_key, mnemonic}`. `TransferService.build_transaction` verifies signature + monotonic nonce. `MintService` creates a coinbase transaction (system signature). + `infrastructure/postgres_wallet_store.py` | `domain/wallet.py`, `infrastructure/postgres_wallet_store.py` |
| I.3.5 | `_mine` applies balance deltas in the same DB transaction as the block insert (idempotent: skip if already applied) | `basic-blockchain.py`, `infrastructure/postgres_wallet_store.py` |
| I.3.6 | Endpoints: `POST /wallets` (returns mnemonic ONCE), `GET /wallets/me`, `POST /transactions` (requires `signature` + `nonce`), `POST /admin/mint`. The previous-plan `POST /wallets/<id>/token` is **removed** — the signature replaces the ephemeral token. | `api/wallet_routes.py` |
| I.3.7 | `_validate_blocks` extension: each tx must reference existing wallets; signature must verify against `wallets.public_key`; nonce must be strictly greater than the last used nonce for that wallet (or the tx is reproducible from the chain itself); total balances == minted supply. | `domain/blockchain.py` |
| I.3.8 | Tests: valid signature accepted; tampered signature rejected (403); nonce replay or decreasing rejected (409); freeze rejected (403); mint ADMIN-only; supply conservation; mnemonic→seed→keypair→sign→verify round-trip. | `tests/test_wallets.py`, `tests/test_transfers.py`, `tests/test_crypto.py`, `tests/test_supply_conservation.py` |
| I.3.9 | Docs: data-model + ER (with `public_key`), api-reference (with "save your mnemonic" warning), BR-WL-01..N (signature mandatory, mnemonic returned once, supply conserved), releases/v0.13.0.md | docs |

**Acceptance:** end-to-end transfer between two users using their mnemonic to sign locally; tampered signature rejected; nonce replay rejected; freeze rejected; mint ADMIN-only; supply conserved; mnemonic returned only on `POST /wallets` and never persisted in DB.

**Phase I.4 implications:** wallet creation flow shows the mnemonic in a forced modal with an "I have saved my recovery phrase" checkbox before the user can proceed. The transfer form takes the mnemonic as a local-only password input, derives the keypair in browser memory (`@scure/bip39` + `@noble/curves`), signs the canonical message, and only sends `{sender_wallet_id, receiver_wallet_id, amount, nonce, signature}` to the backend — the mnemonic itself never crosses the wire on transfer.

### Phase I.4 — Frontend auth + wallet UX (frontend **v0.7.0**)

Branch (frontend repo): `feat/auth-and-wallet-ui`.

Detailed work items (H+B-style breakdown) live in the frontend's own copy of this doc — see `basic-blockchain-frontend/docs/phases/phase-i.md` once Phase I.4 starts.

## Phase J (out of scope, follow-up)

Multi-currency / fungible tokens.

- V013 `currencies` (id, symbol, name, decimals, mintable_by_role).
- V014 `wallet_balances` (wallet_id, currency_id, balance) — replaces the `balance` column in `wallets`.
- Domain: `MintService` and `TransferService` take `currency_id`; transactions gain `currency_id`.
- Frontend: currency picker on transfer form; per-currency balance table.
- Estimated ~10 files; same paired-release / sprint plan / GitFlow pattern as Phase I.

## Traceability

Every commit subject ends with the work-item ID in brackets (e.g. `feat(auth): add JWT helpers [I.1.3]`). Where a logical change naturally spans multiple items in the same file, the suffix lists all (e.g. `[I.1.3, I.1.4]`). Each PR description checklists every ID it carries.
