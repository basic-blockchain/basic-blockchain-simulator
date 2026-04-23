from __future__ import annotations

import os


_DATABASE_URL_DEFAULT = "postgresql://postgres:postgres@localhost:5432/blockchain_simulator"
DATABASE_URL: str = os.environ.get("DATABASE_URL", _DATABASE_URL_DEFAULT)

DIFFICULTY_PREFIX: str = os.environ.get("DIFFICULTY_PREFIX", "00000")

TESTING: bool = os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}
