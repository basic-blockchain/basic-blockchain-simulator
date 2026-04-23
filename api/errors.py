from __future__ import annotations

from quart import Quart, jsonify


def _envelope(message: str, code: str) -> dict[str, str]:
    return {"error": message, "code": code}


def bad_request(message: str, code: str = "BAD_REQUEST") -> tuple:
    return jsonify(_envelope(message, code)), 400


def register_error_handlers(app: Quart) -> None:
    @app.errorhandler(400)
    async def handle_400(exc):
        return jsonify(_envelope(str(exc), "BAD_REQUEST")), 400

    @app.errorhandler(404)
    async def handle_404(exc):
        return jsonify(_envelope("Resource not found", "NOT_FOUND")), 404

    @app.errorhandler(405)
    async def handle_405(exc):
        return jsonify(_envelope("Method not allowed", "METHOD_NOT_ALLOWED")), 405

    @app.errorhandler(500)
    async def handle_500(exc):
        return jsonify(_envelope("Internal server error", "INTERNAL_ERROR")), 500
