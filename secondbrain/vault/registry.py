"""Multi-vault registry: manage multiple brain vaults."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REGISTRY_DB = Path.home() / ".vaultforge" / "registry.db"

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS vaults (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


@dataclass
class VaultEntry:
    id: str
    name: str
    path: str
    is_active: int = 0
    created_at: str = ""


class VaultRegistry:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or REGISTRY_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript(REGISTRY_SCHEMA)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def register(self, vault_id: str, name: str, path: str, activate: bool = False) -> VaultEntry:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            if activate:
                conn.execute("UPDATE vaults SET is_active = 0")
            conn.execute(
                "INSERT OR REPLACE INTO vaults (id, name, path, is_active, created_at) VALUES (?, ?, ?, ?, ?)",
                (vault_id, name, path, 1 if activate else 0, now),
            )
            conn.commit()
        finally:
            conn.close()
        return VaultEntry(id=vault_id, name=name, path=path, is_active=1 if activate else 0, created_at=now)

    def list_vaults(self) -> list[VaultEntry]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM vaults ORDER BY name").fetchall()
            return [VaultEntry(**dict(r)) for r in rows]
        finally:
            conn.close()

    def get_active(self) -> VaultEntry | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM vaults WHERE is_active = 1").fetchone()
            if row is None:
                return None
            return VaultEntry(**dict(row))
        finally:
            conn.close()

    def activate(self, vault_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE vaults SET is_active = 0")
            conn.execute("UPDATE vaults SET is_active = 1 WHERE id = ?", (vault_id,))
            conn.commit()
        finally:
            conn.close()

    def remove(self, vault_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM vaults WHERE id = ?", (vault_id,))
            conn.commit()
        finally:
            conn.close()

    def get_vault(self, vault_id: str) -> VaultEntry | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM vaults WHERE id = ?", (vault_id,)).fetchone()
            if row is None:
                return None
            return VaultEntry(**dict(row))
        finally:
            conn.close()
