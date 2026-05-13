from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse


def _normalise(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class NodeRegistryProtocol(Protocol):
    def add(self, url: str) -> None: ...
    def all(self) -> list[str]: ...
    def count(self) -> int: ...


class InMemoryNodeRegistry:
    def __init__(self) -> None:
        self._nodes: set[str] = set()

    def add(self, url: str) -> None:
        self._nodes.add(_normalise(url))

    def all(self) -> list[str]:
        return sorted(self._nodes)

    def count(self) -> int:
        return len(self._nodes)
