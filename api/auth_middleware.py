"""JWT request middleware (Phase I.1).

Decodes `Authorization: Bearer <token>` on every request and stores the
result on Quart's `g` (request-local). Public routes (chain, health,
auth/*) skip the check; protected routes consume `g.current_user` to
authorise actions.

The middleware does not enforce authentication on its own — it only
populates `g`. Endpoints that require a logged-in user call
`require_auth()` (or, in Phase I.2, `@require_permission(...)`).
"""

from __future__ import annotations

from dataclasses import dataclass

from quart import Quart, abort, g, request

from domain.auth import AuthError, decode_jwt


@dataclass(frozen=True)
class CurrentUser:
    """Lightweight identity envelope attached to `g.current_user` on every
    authenticated request. Keep it framework-free so tests can construct
    one without going through the middleware."""

    user_id: str
    roles: list[str]


# Public routes — request paths the middleware allows through with
# `g.current_user = None`. The list is conservative on purpose: anything
# not enumerated here is treated as protected.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/api/v1/",
        "/api/v1/health",
        "/api/v1/chain",
        "/api/v1/valid",
        "/api/v1/auth/register",
        "/api/v1/auth/activate",
        "/api/v1/auth/login",
        # Legacy unversioned routes preserved for back-compat with v0.10.0
        "/get_chain",
        "/valid",
    }
)


def install_auth_middleware(app: Quart, *, secret: str, algorithm: str = "HS256") -> None:
    """Register the global `before_request` hook on `app`.

    The hook is a no-op for `PUBLIC_PATHS` and for the WebSocket route
    (Quart routes WebSocket lifecycle through a separate hook). For every
    other request it tries to decode the bearer token and either attaches
    the resulting `CurrentUser` to `g.current_user` or aborts with 401 if
    the header is malformed / the token is invalid or expired.

    Endpoints that need only optional auth can read `g.current_user` and
    branch on `is None`. Endpoints that REQUIRE auth call `require_auth()`
    which aborts with 401 when `g.current_user is None`.
    """

    @app.before_request
    async def _read_bearer_token() -> None:  # pragma: no cover — wired by tests
        g.current_user = None
        if request.path in PUBLIC_PATHS:
            return

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            # Anonymous requests are allowed to flow through; the protected
            # route will reject them via `require_auth()`. Not aborting
            # here keeps OPTIONS / preflight requests cheap and lets us
            # add finer per-route control later.
            return

        token = header[len("Bearer "):].strip()
        if not token:
            return

        try:
            payload = decode_jwt(token, secret, algorithm=algorithm)
        except AuthError as exc:
            # Invalid or expired token: reject before the route runs.
            abort(401, description=exc.code)

        g.current_user = CurrentUser(user_id=payload.sub, roles=list(payload.roles))


def require_auth() -> CurrentUser:
    """Helper for protected route handlers.

    Returns the current user or aborts with 401. Use at the top of a
    handler that needs an authenticated identity:

        async def handler():
            user = require_auth()
            ...
    """
    user = getattr(g, "current_user", None)
    if user is None:
        abort(401, description="AUTH_REQUIRED")
    return user
