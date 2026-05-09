# Deferred Gaps and Multi-Currency Evolution Roadmap

This document is a forward-looking reference for engineers working on the
blockchain simulator. It captures use cases from the data-model spec
(`blockchain-data-model/docs/es/SYSTEM_DOCUMENTATION.md`) that are deliberately
out of scope for the current phase, and the phased plan for evolving the
simulator into a multi-currency platform with treasury and exchange-rate
support.

It is intentionally concise and technically precise; it is not a status report.

---

## Deferred Gaps

The gaps below are not implemented in the current simulator phase. Each entry
describes the present state, the reason for deferral, and (where applicable) a
forward-compatible path.

### Gap #1 — Admin-created users with formatted USR-XXXXX IDs

- **Today.** Users self-register only. `user_id` is a hex-32 token used as the
  JWT `sub` claim and as the primary key referenced by roles, wallets,
  `audit_log`, and credentials.
- **Why deferred.** Switching to a `USR-XXXXX` format would force migration of
  the JWT `sub` claim, every foreign key referencing `users.user_id`, and any
  client that has cached or persisted the existing identifier. The opaque hex
  token is also cryptographically stronger than a sequential ID.
- **Future path.** If sequential IDs are required for UX (support tickets,
  human-readable references), introduce a `display_id VARCHAR(16)` column
  carrying the `USR-XXXXX` form as a non-unique alias, while keeping the hex
  token as the primary key and JWT subject.

### Gap #10 — Admin top-up from corporate treasury

- **Today.** Mint creates tokens out of thin air. There is no notion of a
  treasury reserve.
- **Why deferred.** A real top-up requires a multi-wallet ownership model
  (treasury wallets), a true debit-credit ledger entry, and cross-wallet atomic
  transactions. All three are part of the multi-currency / treasury phase
  described below.

### Gap #13 — Admin configures exchange rate and fee for a currency pair

- **Today.** The simulator runs a single currency (`NATIVE`); there is no
  exchange-rate concept.
- **Why deferred.** Blocked by the absence of a multi-currency model. Exchange
  rates require a currency catalog, a rate table, and a fee ledger.

### Gap #14 — Cross-currency transfer applies rate automatically

- **Today.** Transfers operate on a single currency; sender and receiver share
  the same denomination.
- **Why deferred.** Requires (a) the exchange-rate infrastructure described in
  Gap #13, (b) atomic swap semantics that debit the sender in currency A while
  crediting the receiver in currency B, and (c) a dedicated fee collection
  wallet.

### Gap #15 — Admin creates treasury wallets per currency; top-up debits from treasury

- **Today.** No treasury concept and no currency-scoped wallets.
- **Why deferred.** Blocked by the multi-currency model. Treasury must become a
  first-class wallet with a `TREASURY` type and automatic balance enforcement
  on debit.

### Gap #18 — Moderation ops require JWT re-validation (sudo mode, 3 attempts)

- **Today.** Admin endpoints verify permission once per request; there is no
  step-up authentication.
- **Why deferred.** Step-up auth requires a session layer (short-lived sudo
  tokens), an attempt counter (Redis or DB-backed), and a lockout policy. This
  is a non-trivial auth-infrastructure investment. **Prerequisite:** decide on
  the session store (Redis vs. PostgreSQL).

---

## Multi-Currency / Multi-Divisa Evolution Roadmap

The roadmap is split into five additive phases. Each phase is independently
shippable and unblocks the next.

### Phase summary

| Phase | Theme              | Key artifact                                  | Unlocks         |
| ----- | ------------------ | --------------------------------------------- | --------------- |
| MC-1  | Currency catalog   | `currencies` table + FK on `wallets`          | MC-2, MC-3      |
| MC-2  | Treasury wallets   | `wallet_type` enum + `POST /admin/treasury`   | MC-5            |
| MC-3  | Exchange rates     | `exchange_rates` table + admin endpoint       | MC-4            |
| MC-4  | Cross-currency tx  | Transfer engine applies rate + fee atomically | Gaps #13, #14   |
| MC-5  | Treasury top-up    | `POST /admin/wallets/:id/top-up`              | Gaps #10, #15   |

### Phase MC-1 — Currency catalog

- Add `currencies` table:
  `code VARCHAR(10) PRIMARY KEY, name TEXT, decimals SMALLINT, active BOOLEAN`.
- Seed with `NATIVE` as the genesis currency.
- Add a `currency` foreign key on `wallets` referencing `currencies.code`. The
  column already exists as `VARCHAR` but is not catalog-enforced.
- Wallet creation accepts an optional `currency` parameter, defaulting to
  `NATIVE`.
- Permission: `CREATE_CURRENCY` (ADMIN only).

### Phase MC-2 — Treasury wallets

- Add `wallet_type ENUM('USER', 'TREASURY', 'FEE')` to `wallets`.
- Admin endpoint `POST /admin/treasury` creates a treasury wallet for a given
  currency.
- Treasury wallets are system-owned: either no `user_id` FK, or a reserved
  `SYSTEM` user.
- Top-up endpoint stub `POST /admin/wallets/<wallet_id>/top-up` lands here so
  the route is reserved; full implementation arrives in MC-5.

### Phase MC-3 — Exchange rates

- Add `exchange_rates` table:
  `from_currency, to_currency, rate NUMERIC(28,8), fee_rate NUMERIC(8,6), updated_at TIMESTAMPTZ`.
- Admin endpoint `PUT /admin/exchange-rates/:from/:to` sets the rate and fee.
- Rate history: do not overwrite in place; insert new rows and keep prior ones
  for audit, with the latest row selected by `updated_at DESC`.

### Phase MC-4 — Cross-currency transfers

- The transfer endpoint detects `sender.currency != receiver.currency`.
- Applies the rate from MC-3:
  - Debit `amount` from the sender in currency A.
  - Credit `amount * rate * (1 - fee_rate)` to the receiver in currency B.
  - Route the fee to the `FEE` wallet for the `(A, B)` pair.
- The whole movement is atomic: either both legs succeed or neither.
- Emits two transaction records, one per currency movement, linked by a shared
  `swap_id`.

### Phase MC-5 — Treasury top-up

- `POST /admin/wallets/<wallet_id>/top-up`: admin selects a `TREASURY` wallet
  as the funding source.
- Validates that the treasury holds sufficient balance in the matching
  currency.
- Debits the treasury, credits the user wallet, and records the operation in
  `audit_log` with `treasury_wallet_id` captured in the `details` payload.

---

## Architecture Considerations

Multi-currency is non-trivial in the current codebase. Three constraints in
particular need to be addressed before MC-4 can ship:

- **`Transaction.amount` carries no currency.** The field is a `Decimal` and
  the schema has no `currency` column. Adding it is a forward-compatible
  schema extension, but every read/write path and projection must be updated.
- **Block merkle root hashes amounts without currency context.** Cross-currency
  transactions are not meaningful under the current hash unless `currency` is
  included in the leaf preimage. This is a hard-fork-equivalent change for the
  simulator and must be sequenced carefully.
- **secp256k1 signatures cover `sender_wallet_id + receiver_wallet_id + amount + nonce`.**
  Adding `currency` to the signed payload is a breaking change requiring
  wallet re-signing or a versioned signature scheme. Plan a `sig_version` byte
  before MC-4 lands so old and new signatures can coexist during migration.

These three items are the non-negotiable prerequisites for any cross-currency
work; they should be tracked as explicit tickets at the start of MC-1.
