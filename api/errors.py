from __future__ import annotations

from flask import Flask, jsonify


def _envelope(message: str, code: str) -> dict[str, str]:
    return {"error": message, "code": code}


def bad_request(message: str, code: str = "BAD_REQUEST") -> tuple:
    return jsonify(_envelope(message, code)), 400


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def handle_400(exc):
        return jsonify(_envelope(str(exc), "BAD_REQUEST")), 400

    @app.errorhandler(404)
    def handle_404(exc):
        return jsonify(_envelope("Resource not found", "NOT_FOUND")), 404

    @app.errorhandler(405)
    def handle_405(exc):
        return jsonify(_envelope("Method not allowed", "METHOD_NOT_ALLOWED")), 405

    @app.errorhandler(500)
    def handle_500(exc):
        return jsonify(_envelope("Internal server error", "INTERNAL_ERROR")), 500
