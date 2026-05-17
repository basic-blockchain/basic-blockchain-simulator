from __future__ import annotations

import os


DATABASE_URL: str | None = os.environ.get("DATABASE_URL") or None

DIFFICULTY_PREFIX: str = os.environ.get("DIFFICULTY_PREFIX", "00000")

TESTING: bool = os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}

# ── Auth (Phase I) ───────────────────────────────────────────
# JWT_SECRET MUST be set in any non-testing environment. The default below is
# only used when TESTING=true so unit tests can run without env coupling — it
# is rejected at startup otherwise.
# PyJWT warns when the HS256 secret is shorter than 32 bytes; pad the test
# sentinel above the threshold so test runs are warning-free.
JWT_SECRET: str = os.environ.get(
    "JWT_SECRET",
    "" if not TESTING else "test-secret-not-for-production-padding",
)
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_TTL_SECONDS: int = int(os.environ.get("JWT_TTL_SECONDS", "1800"))

BCRYPT_ROUNDS: int = int(os.environ.get("BCRYPT_ROUNDS", "12"))

# ── Exchange feeds (Phase I.4) ─────────────────────────────────
EXCHANGE_FEED_ENABLED: bool = os.environ.get("EXCHANGE_FEED_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
}
EXCHANGE_FEED_INTERVAL_SECONDS: int = int(
    os.environ.get("EXCHANGE_FEED_INTERVAL_SECONDS", "300")
)
EXCHANGE_FEED_PROVIDER: str = os.environ.get("EXCHANGE_FEED_PROVIDER", "BINANCE")
EXCHANGE_FEED_PAIRS: str = os.environ.get("EXCHANGE_FEED_PAIRS", "")

# Username that auto-promotes to ADMIN on first registration. If unset, no
# bootstrap promotion happens — operators must seed an ADMIN through the DB.
BOOTSTRAP_ADMIN_USERNAME: str | None = os.environ.get("BOOTSTRAP_ADMIN_USERNAME") or None

# ── Dashboard quote currency (Phase 6i.1) ───────────────────────
# The currency the admin dashboard converts every balance / movement
# into for its USD-equivalent fields (`balance_usd`, `volume_usd`,
# `amount_usd`). Defaults to USDT because every Binance / Crypto.com
# pair the exchange-rate sync supports is quoted against USDT —
# changing this requires manually setting `X/<quote>` rates for
# every currency in use.
DASHBOARD_QUOTE_CURRENCY: str = (
    os.environ.get("DASHBOARD_QUOTE_CURRENCY", "USDT").strip().upper() or "USDT"
)

# Bootstrap-seed the currencies catalog + a small set of default
# X/USDT rates the first time the simulator boots against an empty
# `currencies` / `exchange_rates` table. Disable to require an
# operator to seed the data via /admin/currencies + /admin/exchange-
# rates manually.
DASHBOARD_BOOTSTRAP_SEED: bool = os.environ.get(
    "DASHBOARD_BOOTSTRAP_SEED", "true"
).lower() in {"1", "true", "yes"}
