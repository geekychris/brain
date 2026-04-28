"""Tests for the compiler pipeline."""

import json
from pathlib import Path

import pytest

from secondbrain.compiler.compile import (
    compile_source,
    compile_all_pending,
    _slugify,
    _truncate,
)
from secondbrain.database import Database, Source, Job
from secondbrain.llm.client import MockLLMClient
from secondbrain.vault.manager import VaultManager
from secondbrain.vault.frontmatter import parse_frontmatter


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
def sample_source(vault, db):
    raw_path = vault.raw_path_for("2026", "04", "kafka-notes.md")
    raw_path.write_text(
        "# Kafka Consumer Groups\n\n"
        "Kafka uses consumer groups to distribute partition ownership. "
        "Each partition is consumed by exactly one consumer in the group. "
        "Rebalancing occurs when consumers join or leave.\n\n"
        "Key concepts: partitions, consumer groups, rebalancing, offsets."
    )
    source = Source(
        id="source-kafka123",
        source_type="markdown",
        original_uri=None,
        raw_path=str(raw_path.relative_to(vault.root)),
        content_hash="abc123",
        imported_at="2026-04-26T10:00:00+00:00",
        title="Kafka Consumer Groups",
    )
    db.add_source(source)
    return source


class TestHelpers:
    def test_slugify(self):
        assert _slugify("Kafka Consumer Groups") == "kafka-consumer-groups"

    def test_truncate_short_text(self):
        assert _truncate("hello", 100) == "hello"

    def test_truncate_long_text(self):
        text = "x" * 10000
        result = _truncate(text, 100)
        assert len(result) < 200
        assert "[... truncated ...]" in result


class TestCompileSource:
    def test_compile_creates_source_note(self, vault, db, llm, sample_source):
        notes = compile_source(sample_source, vault, db, llm)

        assert len(notes) >= 1
        source_notes = [n for n in notes if n.note_type == "source"]
        assert len(source_notes) == 1

        note = source_notes[0]
        assert note.title == "Test Document"  # From mock LLM
        assert note.path.startswith("compiled/sources/")

        # Note file should exist on disk
        note_path = vault.root / note.path
        assert note_path.exists()

        # Should have frontmatter
        content = note_path.read_text()
        fm, body = parse_frontmatter(content)
        assert fm is not None
        assert fm.note_type == "source"
        assert sample_source.id in fm.source_ids

    def test_compile_creates_concept_notes(self, vault, db, llm, sample_source):
        notes = compile_source(sample_source, vault, db, llm)

        concept_notes = [n for n in notes if n.note_type == "concept"]
        assert len(concept_notes) >= 1

        for cn in concept_notes:
            path = vault.root / cn.path
            assert path.exists()
            content = path.read_text()
            fm, _ = parse_frontmatter(content)
            assert fm is not None
            assert fm.note_type == "concept"

    def test_compile_registers_entities(self, vault, db, llm, sample_source):
        compile_source(sample_source, vault, db, llm)
        entities = db.list_entities()
        assert len(entities) >= 1

    def test_compile_links_note_to_source(self, vault, db, llm, sample_source):
        notes = compile_source(sample_source, vault, db, llm)
        source_note = [n for n in notes if n.note_type == "source"][0]
        source_ids = db.get_note_sources(source_note.id)
        assert sample_source.id in source_ids

    def test_compile_nonexistent_raw_returns_empty(self, vault, db, llm):
        source = Source(
            id="source-missing",
            source_type="text",
            raw_path="raw/missing.md",
            content_hash="missing",
            imported_at="",
        )
        db.add_source(source)
        notes = compile_source(source, vault, db, llm)
        assert notes == []


class TestCompileAllPending:
    def test_compile_all_pending(self, vault, db, llm, sample_source):
        # Add a compile job
        job = Job(
            id=f"compile-{sample_source.id}",
            job_type="compile",
            status="pending",
            input_json=json.dumps({"source_id": sample_source.id}),
            created_at="2026-04-26T10:00:00+00:00",
            updated_at="2026-04-26T10:00:00+00:00",
        )
        db.add_job(job)

        notes = compile_all_pending(vault, db, llm)
        assert len(notes) >= 1

        # Source should no longer be uncompiled
        assert len(db.get_uncompiled_sources()) == 0

    def test_compile_all_pending_no_sources(self, vault, db, llm):
        notes = compile_all_pending(vault, db, llm)
        assert notes == []

    def test_concept_note_not_overwritten(self, vault, db, llm, sample_source):
        # Pre-create a concept note
        concept_path = vault.compiled_path_for("concepts", "related-concept-a")
        vault.write_note(concept_path, "# Existing concept\n\nUser-authored content.")

        notes = compile_source(sample_source, vault, db, llm)
        # The existing concept note should not be overwritten
        content = concept_path.read_text()
        assert "User-authored content" in content
