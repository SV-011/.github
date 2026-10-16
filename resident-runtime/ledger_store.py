#!/usr/bin/env python3
"""File-backed ledger store addressed by key.

The caller supplies the root. Nothing here derives a location from the host,
and nothing here selects admission, routing, or execution. A disposition
recorded through this store is transition evidence. Authority and time stay
constraint variables on the receipt a caller writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEAD_KEY = "head.json"


def receipt_key(digest: str) -> str:
    hex_digest = digest[7:] if digest.startswith("sha256:") else digest
    return "receipts/" + hex_digest + ".json"


def parse_locator(value: str) -> Path:
    return Path(value)


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key

    def locator(self, key: str) -> str:
        return str(self._path(key))

    def get(self, key: str) -> Any:
        path = self._path(key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, key: str, document: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8")


def open_store(root: Path | str) -> Store:
    return Store(Path(root))
