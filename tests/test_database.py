"""Tests for the SQLite database layer."""

import json
import tempfile
from pathlib import Path

import pytest

from secondbrain.database import Database, Source, Note, Entity, Job, LLMConfig


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.init_schema()
    return db


@pytest.fixture
def sample_source():
    return Source(
        id="source-abc123",
        source_type="pdf",
        original_uri="/tmp/test.pdf",
        raw_path="raw/2026/04/test.pdf",
        content_hash="deadbeef" * 8,
        imported_at="2026-04-26T10:00:00+00:00",
        title="Test Document",
    )


@pytest.fixture
def sample_note():
    return Note(
        id="note-test-doc",
        path="compiled/sources/test-doc.md",
        title="Test Document",
        note_type="source",
        created_at="2026-04-26T10:00:00+00:00",
        updated_at="2026-04-26T10:00:00+00:00",
        content_hash="abc123" * 10,
    )


class TestDatabaseSchema:
    def test_init_schema_creates_tables(self, db):
        with db.connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {r["name"] for r in tables}
            assert "sources" in table_names
            assert "notes" in table_names
            assert "note_sources" in table_names
            assert "links" in table_names
            assert "entities" in table_names
            assert "jobs" in table_names

    def test_init_schema_idempotent(self, db):
        db.init_schema()
        db.init_schema()  # Should not raise


class TestSources:
    def test_add_and_get_source(self, db, sample_source):
        db.add_source(sample_source)
        result = db.get_source(sample_source.id)
        assert result is not None
        assert result.id == sample_source.id
        assert result.title == "Test Document"
        assert result.source_type == "pdf"

    def test_get_source_not_found(self, db):
        assert db.get_source("nonexistent") is None

    def test_get_source_by_hash(self, db, sample_source):
        db.add_source(sample_source)
        result = db.get_source_by_hash(sample_source.content_hash)
        assert result is not None
        assert result.id == sample_source.id

    def test_list_sources(self, db, sample_source):
        db.add_source(sample_source)
        sources = db.list_sources()
        assert len(sources) == 1
        assert sources[0].id == sample_source.id

    def test_list_sources_by_type(self, db, sample_source):
        db.add_source(sample_source)
        assert len(db.list_sources("pdf")) == 1
        assert len(db.list_sources("url")) == 0

    def test_upsert_source(self, db, sample_source):
        db.add_source(sample_source)
        sample_source.title = "Updated Title"
        db.add_source(sample_source)
        result = db.get_source(sample_source.id)
        assert result.title == "Updated Title"


class TestNotes:
    def test_add_and_get_note(self, db, sample_note):
        db.add_note(sample_note)
        result = db.get_note(sample_note.id)
        assert result is not None
        assert result.title == "Test Document"

    def test_get_note_by_path(self, db, sample_note):
        db.add_note(sample_note)
        result = db.get_note_by_path(sample_note.path)
        assert result is not None
        assert result.id == sample_note.id

    def test_list_notes_by_type(self, db, sample_note):
        db.add_note(sample_note)
        assert len(db.list_notes("source")) == 1
        assert len(db.list_notes("concept")) == 0

    def test_search_notes_fulltext(self, db, sample_note):
        db.add_note(sample_note)
        results = db.search_notes_fulltext("Test")
        assert len(results) == 1
        assert results[0].title == "Test Document"

    def test_search_notes_no_match(self, db, sample_note):
        db.add_note(sample_note)
        results = db.search_notes_fulltext("nonexistent")
        assert len(results) == 0


class TestNoteSources:
    def test_add_note_source_link(self, db, sample_source, sample_note):
        db.add_source(sample_source)
        db.add_note(sample_note)
        db.add_note_source(sample_note.id, sample_source.id, "direct")
        sources = db.get_note_sources(sample_note.id)
        assert sample_source.id in sources

    def test_uncompiled_sources(self, db, sample_source, sample_note):
        db.add_source(sample_source)
        uncompiled = db.get_uncompiled_sources()
        assert len(uncompiled) == 1

        db.add_note(sample_note)
        db.add_note_source(sample_note.id, sample_source.id)
        uncompiled = db.get_uncompiled_sources()
        assert len(uncompiled) == 0


class TestLinks:
    def test_add_and_get_links(self, db):
        note1 = Note(id="n1", path="a.md", title="A", note_type="concept",
                     created_at="", updated_at="", content_hash="")
        note2 = Note(id="n2", path="b.md", title="B", note_type="concept",
                     created_at="", updated_at="", content_hash="")
        db.add_note(note1)
        db.add_note(note2)
        db.add_link("n1", "n2", "backlink", 0.9)

        outbound = db.get_outbound_links("n1")
        assert len(outbound) == 1
        assert outbound[0]["to_note_id"] == "n2"

        inbound = db.get_inbound_links("n2")
        assert len(inbound) == 1
        assert inbound[0]["from_note_id"] == "n1"


class TestEntities:
    def test_add_and_list_entities(self, db):
        entity = Entity(
            id="tech.kafka",
            name="Kafka",
            entity_type="technology",
            aliases_json=json.dumps(["Apache Kafka"]),
        )
        db.add_entity(entity)
        entities = db.list_entities("technology")
        assert len(entities) == 1
        assert entities[0].name == "Kafka"


class TestJobs:
    def test_add_and_get_pending_jobs(self, db):
        job = Job(
            id="job-1",
            job_type="compile",
            status="pending",
            input_json='{"source_id": "s1"}',
            created_at="2026-04-26T10:00:00+00:00",
            updated_at="2026-04-26T10:00:00+00:00",
        )
        db.add_job(job)
        pending = db.get_pending_jobs()
        assert len(pending) == 1

    def test_update_job_status(self, db):
        job = Job(
            id="job-1", job_type="compile", status="pending",
            input_json="{}", created_at="", updated_at="",
        )
        db.add_job(job)
        db.update_job_status("job-1", "completed", '{"notes": 2}')
        pending = db.get_pending_jobs()
        assert len(pending) == 0


class TestLLMConfigs:
    def test_add_and_get_config(self, db):
        config = LLMConfig(
            id="spark", name="Spark Nemotron", backend_type="llamacpp",
            base_url="http://spark.local:30000",
            model="Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf", is_active=1,
        )
        db.add_llm_config(config)
        result = db.get_llm_config("spark")
        assert result is not None
        assert result.name == "Spark Nemotron"
        assert result.base_url == "http://spark.local:30000"

    def test_list_configs(self, db):
        db.add_llm_config(LLMConfig(id="a", name="A", backend_type="llamacpp",
                                     base_url="http://a", model="m1"))
        db.add_llm_config(LLMConfig(id="b", name="B", backend_type="ollama",
                                     base_url="http://b", model="m2"))
        configs = db.list_llm_configs()
        assert len(configs) == 2

    def test_activate_config(self, db):
        db.add_llm_config(LLMConfig(id="a", name="A", backend_type="llamacpp",
                                     base_url="http://a", model="m1", is_active=1))
        db.add_llm_config(LLMConfig(id="b", name="B", backend_type="llamacpp",
                                     base_url="http://b", model="m2", is_active=0))
        assert db.get_active_llm_config().id == "a"

        db.activate_llm_config("b")
        active = db.get_active_llm_config()
        assert active.id == "b"
        # Old one should be deactivated
        old = db.get_llm_config("a")
        assert old.is_active == 0

    def test_delete_config(self, db):
        db.add_llm_config(LLMConfig(id="x", name="X", backend_type="llamacpp",
                                     base_url="http://x", model="m"))
        db.delete_llm_config("x")
        assert db.get_llm_config("x") is None

    def test_no_active_config(self, db):
        assert db.get_active_llm_config() is None
