"""HTTP-layer plumbing for the RBAC contract (Phase I.2).

Provides `@require_permission(perm)` for route handlers. The decorator
reads `g.current_user` (populated by `api.auth_middleware`), resolves
the permission against the static defaults plus any DB-backed overrides
the app factory registered via `set_permission_resolver`, and aborts
with HTTP 403 / `code: FORBIDDEN` when the user lacks it.
"""

from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable

from quart import abort, g

from domain.permissions import Permission, has_permission


# Module-level resolver hooks injected by `create_app` so the decorator
# can stay a pure function-decorator while the role/user override
# lookups happen against the live persistence layer.
_role_overrides_loader: Callable[[], dict[str, set[str]]] | None = None
_user_overrides_loader: Callable[[str], set[str]] | None = None


def set_permission_resolver(
    *,
    role_overrides: Callable[[], dict[str, set[str]]] | None = None,
    user_overrides: Callable[[str], set[str]] | None = None,
) -> None:
    """Register the loader callbacks the decorator uses to fetch DB-backed
    overrides. `role_overrides()` returns a snapshot of the per-role
    overrides; `user_overrides(user_id)` returns the direct grants for one
    user. Either may be None — in which case the decorator falls back to
    the hardcoded ROLE_PERMISSIONS baseline only.

    Tests can call this with `None` to reset state between cases.
    """
    global _role_overrides_loader, _user_overrides_loader
    _role_overrides_loader = role_overrides
    _user_overrides_loader = user_overrides


def require_permission(permission: Permission):
    """Route decorator: aborts with 403 / FORBIDDEN unless `g.current_user`
    holds `permission`. Aborts with 401 / AUTH_REQUIRED if there is no
    authenticated user on the request.

    Usage:

        @api_v1.route("/admin/users", methods=["GET"])
        @require_permission(Permission.VIEW_USERS)
        async def list_users():
            ...
    """

    def decorator(handler: Callable[..., Awaitable]):
        @wraps(handler)
        async def wrapper(*args, **kwargs):
            current = getattr(g, "current_user", None)
            if current is None:
                abort(401, description="AUTH_REQUIRED")

            role_overrides = _role_overrides_loader() if _role_overrides_loader else None
            user_overrides_map = None
            if _user_overrides_loader is not None:
                grants = _user_overrides_loader(current.user_id)
                if grants:
                    user_overrides_map = {current.user_id: grants}

            allowed = has_permission(
                user_id=current.user_id,
                roles=list(current.roles),
                permission=permission.value,
                role_overrides=role_overrides,
                user_overrides=user_overrides_map,
            )
            if not allowed:
                abort(403, description="FORBIDDEN")
            return await handler(*args, **kwargs)

        return wrapper

    return decorator
