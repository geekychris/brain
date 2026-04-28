"""Tests for vault health checks."""

from pathlib import Path

import pytest

from secondbrain.database import Database, Note, Source
from secondbrain.health.checks import run_health_check, _similar_titles, HealthReport
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


def _create_note(vault, db, title, note_type="concept", tags=None, source_ids=None,
                 links=None, created="2026-04-26", updated="2026-04-26", include_summary=True):
    slug = title.lower().replace(" ", "-")
    fm = NoteFrontmatter(
        title=title,
        note_type=note_type,
        tags=tags or [],
        source_ids=source_ids or [],
        created=created,
        updated=updated,
    )
    body_parts = [f"# {title}\n"]
    if include_summary:
        body_parts.append("## Summary\n")
        body_parts.append("This is a summary.\n")
    if links:
        body_parts.append("## Related Notes\n")
        for link in links:
            body_parts.append(f"- [[{link}]]")
        body_parts.append("")
    body = "\n".join(body_parts)
    content = build_note(fm, body)
    path = vault.compiled_path_for(note_type + "s", slug)
    vault.write_note(path, content)

    note = Note(
        id=f"note-{slug}",
        path=str(path.relative_to(vault.root)),
        title=title,
        note_type=note_type,
        created_at=f"{created}T00:00:00+00:00",
        updated_at=f"{updated}T00:00:00+00:00",
        content_hash="test",
    )
    db.add_note(note)
    return note


class TestSimilarTitles:
    def test_identical(self):
        assert _similar_titles("Kafka", "Kafka")

    def test_case_insensitive(self):
        assert _similar_titles("Kafka", "kafka")

    def test_substring(self):
        assert _similar_titles("Kafka", "Apache Kafka")

    def test_different(self):
        assert not _similar_titles("Kafka", "Redis")

    def test_high_word_overlap(self):
        assert _similar_titles("Kafka Consumer Groups", "Consumer Groups Kafka")

    def test_low_word_overlap(self):
        assert not _similar_titles("Kafka Consumer Groups", "Redis Pub Sub")


class TestHealthChecks:
    def test_empty_vault_no_issues(self, vault, db):
        report = run_health_check(vault, db)
        assert report.total_issues == 0

    def test_detect_orphan_notes(self, vault, db):
        _create_note(vault, db, "Orphan Note", source_ids=["s1"])
        report = run_health_check(vault, db)
        assert "Orphan Note" in report.orphan_notes

    def test_linked_notes_not_orphans(self, vault, db):
        _create_note(vault, db, "Note A", links=["Note B"], source_ids=["s1"])
        _create_note(vault, db, "Note B", links=["Note A"], source_ids=["s1"])
        report = run_health_check(vault, db)
        assert "Note A" not in report.orphan_notes
        assert "Note B" not in report.orphan_notes

    def test_detect_broken_links(self, vault, db):
        _create_note(vault, db, "Note With Broken Link", links=["Missing Page"], source_ids=["s1"])
        report = run_health_check(vault, db)
        assert len(report.broken_links) >= 1
        targets = [bl["target"] for bl in report.broken_links]
        assert "Missing Page" in targets

    def test_detect_duplicate_candidates(self, vault, db):
        _create_note(vault, db, "Kafka", source_ids=["s1"])
        _create_note(vault, db, "Apache Kafka", note_type="source", source_ids=["s2"])
        report = run_health_check(vault, db)
        assert len(report.duplicate_candidates) >= 1

    def test_detect_stale_notes(self, vault, db):
        _create_note(vault, db, "Old Note", updated="2024-01-01", source_ids=["s1"])
        report = run_health_check(vault, db)
        assert "Old Note" in report.stale_notes

    def test_recent_note_not_stale(self, vault, db):
        _create_note(vault, db, "Fresh Note", updated="2026-04-26", source_ids=["s1"])
        report = run_health_check(vault, db)
        assert "Fresh Note" not in report.stale_notes

    def test_detect_missing_provenance(self, vault, db):
        _create_note(vault, db, "No Source", source_ids=[])
        report = run_health_check(vault, db)
        assert "No Source" in report.missing_provenance

    def test_detect_weak_summaries(self, vault, db):
        _create_note(vault, db, "No Summary", include_summary=False, source_ids=["s1"])
        report = run_health_check(vault, db)
        assert "No Summary" in report.weak_summaries

    def test_detect_uncompiled_sources(self, vault, db):
        source = Source(
            id="source-uncompiled",
            source_type="pdf",
            raw_path="raw/test.pdf",
            content_hash="test123",
            imported_at="2026-04-26T10:00:00+00:00",
            title="Uncompiled",
        )
        db.add_source(source)
        report = run_health_check(vault, db)
        assert report.uncompiled_sources == 1


class TestHealthReport:
    def test_report_to_markdown(self, vault, db):
        _create_note(vault, db, "Orphan", source_ids=["s1"])
        report = run_health_check(vault, db)
        md = report.to_markdown()
        assert "# Vault Health Report" in md

    def test_clean_report_markdown(self, vault, db):
        report = run_health_check(vault, db)
        md = report.to_markdown()
        assert "All clear!" in md

    def test_total_issues_count(self):
        report = HealthReport(
            orphan_notes=["a", "b"],
            broken_links=[{"source": "a", "target": "b"}],
            uncompiled_sources=3,
        )
        assert report.total_issues == 6
