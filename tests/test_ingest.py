"""Tests for the ingestion pipeline."""

from pathlib import Path

import pytest

from secondbrain.database import Database
from secondbrain.ingest.pipeline import (
    detect_source_type,
    extract_text_from_file,
    ingest_file,
    ingest_text,
    _content_hash,
    _slugify,
)
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


class TestHelpers:
    def test_detect_source_type_pdf(self):
        assert detect_source_type(Path("doc.pdf")) == "pdf"

    def test_detect_source_type_markdown(self):
        assert detect_source_type(Path("note.md")) == "markdown"

    def test_detect_source_type_code(self):
        assert detect_source_type(Path("main.py")) == "code"

    def test_detect_source_type_unknown(self):
        assert detect_source_type(Path("file.xyz")) == "text"

    def test_slugify(self):
        assert _slugify("Kafka Consumer Groups") == "kafka-consumer-groups"
        assert _slugify("Hello World!") == "hello-world"

    def test_content_hash_deterministic(self):
        data = b"hello world"
        assert _content_hash(data) == _content_hash(data)

    def test_content_hash_different_for_different_content(self):
        assert _content_hash(b"hello") != _content_hash(b"world")


class TestExtractText:
    def test_extract_text_from_markdown(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("# Hello\n\nThis is a test.")
        text = extract_text_from_file(md)
        assert "Hello" in text
        assert "This is a test" in text

    def test_extract_text_from_text_file(self, tmp_path):
        txt = tmp_path / "test.txt"
        txt.write_text("Plain text content here.")
        text = extract_text_from_file(txt)
        assert "Plain text content" in text


class TestIngestFile:
    def test_ingest_markdown_file(self, tmp_path, vault, db):
        md = tmp_path / "kafka-notes.md"
        md.write_text("# Kafka Notes\n\nKafka is a distributed event log.")

        source = ingest_file(md, vault, db, title="Kafka Notes")

        assert source.id.startswith("source-")
        assert source.source_type == "markdown"
        assert source.title == "Kafka Notes"

        # Raw file should be copied
        raw_path = vault.root / source.raw_path
        assert raw_path.exists()

        # Source should be in DB
        assert db.get_source(source.id) is not None

        # Compile job should be queued
        jobs = db.get_pending_jobs("compile")
        assert len(jobs) == 1

    def test_ingest_text_file(self, tmp_path, vault, db):
        txt = tmp_path / "notes.txt"
        txt.write_text("Some plain text notes about distributed systems.")

        source = ingest_file(txt, vault, db)
        assert source.source_type == "text"
        assert db.get_source(source.id) is not None

    def test_ingest_duplicate_skips(self, tmp_path, vault, db):
        md = tmp_path / "test.md"
        md.write_text("Same content here.")

        s1 = ingest_file(md, vault, db)
        s2 = ingest_file(md, vault, db)

        assert s1.id == s2.id
        sources = db.list_sources()
        assert len(sources) == 1


class TestIngestText:
    def test_ingest_text_directly(self, vault, db):
        source = ingest_text(
            "Quick note about event sourcing patterns.",
            "Event Sourcing",
            vault,
            db,
        )
        assert source.source_type == "text"
        assert source.title == "Event Sourcing"

        raw_path = vault.root / source.raw_path
        assert raw_path.exists()
        assert "event sourcing" in raw_path.read_text().lower()

    def test_ingest_text_duplicate_skips(self, vault, db):
        text = "Unique content here."
        s1 = ingest_text(text, "Note 1", vault, db)
        s2 = ingest_text(text, "Note 1", vault, db)
        assert s1.id == s2.id
