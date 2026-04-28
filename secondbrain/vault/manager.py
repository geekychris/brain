"""Vault filesystem management."""

from __future__ import annotations

from pathlib import Path

VAULT_DIRS = [
    "inbox/quick-notes",
    "inbox/web-clips",
    "inbox/pdfs",
    "inbox/transcripts",
    "raw",
    "compiled/concepts",
    "compiled/projects",
    "compiled/people",
    "compiled/papers",
    "compiled/books",
    "compiled/meetings",
    "compiled/decisions",
    "compiled/maps",
    "compiled/synthesis",
    "compiled/sources",
    "daily",
    "system/schemas",
    "system/prompts",
    "system/health-reports",
    "system/entity-registry",
]


class VaultManager:
    def __init__(self, vault_path: Path) -> None:
        self.root = vault_path
        self.db_path = self.root / "system" / "vaultforge.db"

    def init(self) -> None:
        for d in VAULT_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def compiled_dir(self) -> Path:
        return self.root / "compiled"

    @property
    def daily_dir(self) -> Path:
        return self.root / "daily"

    @property
    def system_dir(self) -> Path:
        return self.root / "system"

    def raw_path_for(self, year: str, month: str, filename: str) -> Path:
        p = self.raw_dir / year / month
        p.mkdir(parents=True, exist_ok=True)
        return p / filename

    def compiled_path_for(self, note_type: str, slug: str) -> Path:
        p = self.compiled_dir / note_type
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{slug}.md"

    def list_compiled_notes(self) -> list[Path]:
        return sorted(self.compiled_dir.rglob("*.md"))

    def list_all_notes(self) -> list[Path]:
        notes = list(self.compiled_dir.rglob("*.md"))
        notes.extend(self.daily_dir.rglob("*.md"))
        return sorted(notes)

    def read_note(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_note(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
