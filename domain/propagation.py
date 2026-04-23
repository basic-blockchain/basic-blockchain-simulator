from __future__ import annotations

import json
import urllib.error
import urllib.request

from .models import Transaction
from .node_registry import NodeRegistryProtocol


class PropagationService:
    def __init__(self, registry: NodeRegistryProtocol, timeout: int = 3) -> None:
        self._registry = registry
        self._timeout = timeout

    def _post(self, url: str, payload: dict) -> None:
        if not url.startswith(("http://", "https://")):
            return
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "X-Propagated": "1"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout):  # nosec B310 — scheme validated above
                pass
        except Exception:
            pass

    def _get(self, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            return
        try:
            with urllib.request.urlopen(url, timeout=self._timeout):  # nosec B310 — scheme validated above
                pass
        except Exception:
            pass

    def broadcast_transaction(self, tx: Transaction) -> None:
        for node in self._registry.all():
            self._post(f"{node}/api/v1/transactions", tx.to_dict())

    def notify_resolve(self) -> None:
        for node in self._registry.all():
            self._get(f"{node}/api/v1/nodes/resolve")
