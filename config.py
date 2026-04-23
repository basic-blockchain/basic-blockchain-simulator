from __future__ import annotations

import os


DATABASE_URL: str | None = os.environ.get("DATABASE_URL") or None

DIFFICULTY_PREFIX: str = os.environ.get("DIFFICULTY_PREFIX", "00000")

TESTING: bool = os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}
