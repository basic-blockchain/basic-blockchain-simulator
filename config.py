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

# Username that auto-promotes to ADMIN on first registration. If unset, no
# bootstrap promotion happens — operators must seed an ADMIN through the DB.
BOOTSTRAP_ADMIN_USERNAME: str | None = os.environ.get("BOOTSTRAP_ADMIN_USERNAME") or None
