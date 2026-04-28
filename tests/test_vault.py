"""Tests for vault management and frontmatter."""

from pathlib import Path

import pytest

from secondbrain.vault.manager import VaultManager, VAULT_DIRS
from secondbrain.vault.frontmatter import (
    NoteFrontmatter,
    parse_frontmatter,
    render_frontmatter,
    build_note,
)


@pytest.fixture
def vault(tmp_path):
    v = VaultManager(tmp_path / "testvault")
    v.init()
    return v


class TestVaultManager:
    def test_init_creates_directories(self, vault):
        for d in VAULT_DIRS:
            assert (vault.root / d).is_dir()

    def test_init_idempotent(self, vault):
        vault.init()  # Should not raise

    def test_raw_path_for(self, vault):
        p = vault.raw_path_for("2026", "04", "test.pdf")
        assert p.parent.is_dir()
        assert p.name == "test.pdf"
        assert "2026" in str(p)
        assert "04" in str(p)

    def test_compiled_path_for(self, vault):
        p = vault.compiled_path_for("concepts", "kafka")
        assert p.parent.is_dir()
        assert p.name == "kafka.md"

    def test_write_and_read_note(self, vault):
        path = vault.compiled_path_for("concepts", "test")
        vault.write_note(path, "# Test\n\nHello world")
        content = vault.read_note(path)
        assert "# Test" in content
        assert "Hello world" in content

    def test_list_compiled_notes(self, vault):
        vault.write_note(vault.compiled_path_for("concepts", "a"), "# A")
        vault.write_note(vault.compiled_path_for("sources", "b"), "# B")
        notes = vault.list_compiled_notes()
        assert len(notes) == 2

    def test_list_all_notes_includes_daily(self, vault):
        vault.write_note(vault.compiled_path_for("concepts", "a"), "# A")
        daily = vault.daily_dir / "2026-04-26.md"
        vault.write_note(daily, "# Daily")
        notes = vault.list_all_notes()
        assert len(notes) == 2

    def test_db_path(self, vault):
        assert vault.db_path == vault.root / "system" / "vaultforge.db"


class TestFrontmatter:
    def test_render_and_parse_roundtrip(self):
        fm = NoteFrontmatter(
            title="Kafka Consumer Groups",
            note_type="concept",
            aliases=["consumer groups", "CG"],
            source_ids=["source-abc123"],
            tags=["kafka", "distributed-systems"],
            confidence="high",
            created="2026-04-26",
            updated="2026-04-26",
        )
        rendered = render_frontmatter(fm)
        assert "---" in rendered
        assert "Kafka Consumer Groups" in rendered

        parsed, body = parse_frontmatter(rendered + "\n# Hello\n")
        assert parsed is not None
        assert parsed.title == "Kafka Consumer Groups"
        assert parsed.note_type == "concept"
        assert "consumer groups" in parsed.aliases
        assert "kafka" in parsed.tags
        assert parsed.confidence == "high"

    def test_parse_no_frontmatter(self):
        fm, body = parse_frontmatter("# Just a heading\n\nNo frontmatter here.")
        assert fm is None
        assert "Just a heading" in body

    def test_build_note(self):
        fm = NoteFrontmatter(title="Test", note_type="source", created="2026-04-26", updated="2026-04-26")
        note = build_note(fm, "# Test\n\nBody text.")
        assert note.startswith("---\n")
        assert "# Test" in note
        assert "Body text." in note

    def test_render_minimal_frontmatter(self):
        fm = NoteFrontmatter(title="Minimal", note_type="source")
        rendered = render_frontmatter(fm)
        assert "title: Minimal" in rendered
        assert "type: source" in rendered
        # No aliases or source_ids since they're empty
        assert "aliases" not in rendered
        assert "source_ids" not in rendered

    def test_extra_fields_preserved(self):
        fm = NoteFrontmatter(
            title="Extra",
            note_type="source",
            extra={"privacy": "private", "llm_access": "blocked"},
        )
        rendered = render_frontmatter(fm)
        assert "privacy: private" in rendered
        assert "llm_access: blocked" in rendered
