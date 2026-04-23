from __future__ import annotations

import time
from collections import deque
from functools import wraps
from typing import Callable

from quart import jsonify


def rate_limit(max_calls: int, period_seconds: float) -> Callable:
    """Sliding-window rate limiter keyed per endpoint (process-wide).

    Suitable for single-process dev/educational use. For multi-worker
    deployments, replace with a Redis-backed counter.
    """
    timestamps: deque[float] = deque()

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            now = time.monotonic()
            cutoff = now - period_seconds

            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()

            if len(timestamps) >= max_calls:
                retry_after = int(period_seconds - (now - timestamps[0])) + 1
                response = jsonify(
                    {
                        "error": "Too many requests — mining rate limit exceeded",
                        "code": "RATE_LIMITED",
                        "retry_after_seconds": retry_after,
                    }
                )
                response.headers["Retry-After"] = str(retry_after)
                return response, 429

            timestamps.append(now)
            return await fn(*args, **kwargs)

        return wrapper

    return decorator
