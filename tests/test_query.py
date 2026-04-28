"""Tests for the query engine."""

import json
from pathlib import Path

import pytest

from secondbrain.database import Database, Note
from secondbrain.llm.client import MockLLMClient
from secondbrain.query.engine import search_vault, ask_vault, _extract_excerpt
from secondbrain.vault.frontmatter import NoteFrontmatter, build_note
from secondbrain.vault.manager import VaultManager


@pytest.fixture
def vault(tmp_path):
    v = VaultManager(tmp_path / "vault")
    v.init()
    return v


@pytest.fixture
def db(vault):
    d = Database(vault.db_path)
    d.init_schema()
    return d


@pytest.fixture
def llm():
    return MockLLMClient()


@pytest.fixture
def populated_vault(vault, db):
    """Create a vault with a few notes for searching."""
    notes_data = [
        ("Kafka Consumer Groups", "concept", ["kafka", "distributed-systems"],
         "Kafka uses consumer groups to distribute partition ownership across consumers."),
        ("Event Sourcing", "concept", ["architecture", "events"],
         "Event sourcing stores state as a sequence of events rather than current state."),
        ("NATS JetStream", "concept", ["messaging", "nats"],
         "NATS JetStream provides persistent messaging with at-least-once delivery."),
    ]
    for title, note_type, tags, body_text in notes_data:
        slug = title.lower().replace(" ", "-")
        fm = NoteFrontmatter(
            title=title,
            note_type=note_type,
            tags=tags,
            source_ids=["source-test"],
            created="2026-04-26",
            updated="2026-04-26",
        )
        body = f"# {title}\n\n## Summary\n\n{body_text}\n"
        content = build_note(fm, body)
        path = vault.compiled_path_for("concepts", slug)
        vault.write_note(path, content)

        note = Note(
            id=f"note-{slug}",
            path=str(path.relative_to(vault.root)),
            title=title,
            note_type=note_type,
            created_at="2026-04-26T10:00:00+00:00",
            updated_at="2026-04-26T10:00:00+00:00",
            content_hash="test",
        )
        db.add_note(note)

    return vault, db


class TestExtractExcerpt:
    def test_extract_excerpt_with_match(self):
        content = "Some text before. Kafka uses consumer groups for distribution. Some text after."
        excerpt = _extract_excerpt(content, ["kafka", "consumer"])
        assert "Kafka" in excerpt or "kafka" in excerpt.lower()

    def test_extract_excerpt_no_match(self):
        content = "Nothing relevant here at all."
        excerpt = _extract_excerpt(content, ["nonexistent"])
        assert len(excerpt) > 0  # Should return beginning of content


class TestSearchVault:
    def test_search_finds_matching_note(self, populated_vault):
        vault, db = populated_vault
        results = search_vault("Kafka", vault, db)
        assert len(results) >= 1
        titles = [r.note.title for r in results]
        assert "Kafka Consumer Groups" in titles

    def test_search_ranks_title_match_higher(self, populated_vault):
        vault, db = populated_vault
        results = search_vault("Kafka Consumer Groups", vault, db)
        assert results[0].note.title == "Kafka Consumer Groups"

    def test_search_finds_content_match(self, populated_vault):
        vault, db = populated_vault
        results = search_vault("partition ownership", vault, db)
        assert len(results) >= 1

    def test_search_no_results(self, populated_vault):
        vault, db = populated_vault
        results = search_vault("quantum computing", vault, db)
        assert len(results) == 0

    def test_search_multiple_results(self, populated_vault):
        vault, db = populated_vault
        # "messaging" and "delivery" should match NATS, potentially others
        results = search_vault("messaging delivery", vault, db)
        assert len(results) >= 1


class TestAskVault:
    def test_ask_with_context(self, populated_vault, llm):
        vault, db = populated_vault
        answer = ask_vault("What is Kafka?", vault, db, llm)
        assert answer.text
        assert answer.confidence in ("high", "medium", "low")
        assert answer.answer_type in ("sourced", "synthesized", "speculative", "unknown", "contradictory")

    def test_ask_no_results(self, llm):
        vault = VaultManager(Path("/tmp/empty_vault_test"))
        vault.init()
        db = Database(vault.db_path)
        db.init_schema()

        answer = ask_vault("What is anything?", vault, db, llm)
        assert answer.answer_type == "unknown"
        assert "No relevant notes" in answer.text

    def test_ask_returns_sources(self, populated_vault, llm):
        vault, db = populated_vault
        answer = ask_vault("Tell me about event sourcing", vault, db, llm)
        # Mock LLM returns sources in its response
        assert isinstance(answer.sources, list)
