"""Tests for the CLI commands."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from secondbrain.cli import app
from secondbrain.database import Database
from secondbrain.vault.manager import VaultManager


runner = CliRunner()


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "test_vault"


class TestInitCommand:
    def test_init_creates_vault(self, vault_path):
        result = runner.invoke(app, ["init", str(vault_path)])
        assert result.exit_code == 0
        assert "Vault initialized" in result.output
        assert (vault_path / "inbox").is_dir()
        assert (vault_path / "raw").is_dir()
        assert (vault_path / "compiled").is_dir()
        assert (vault_path / "system").is_dir()

    def test_init_creates_database(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        db_path = vault_path / "system" / "vaultforge.db"
        assert db_path.exists()

    def test_init_idempotent(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        result = runner.invoke(app, ["init", str(vault_path)])
        assert result.exit_code == 0


class TestIngestCommand:
    def test_ingest_file(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        test_file = tmp_path / "test.md"
        test_file.write_text("# Test\n\nTest content.")

        result = runner.invoke(app, ["ingest", str(test_file), "--vault", str(vault_path)])
        assert result.exit_code == 0
        assert "Ingested" in result.output

    def test_ingest_nonexistent_file(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        result = runner.invoke(app, ["ingest", "/nonexistent/file.md", "--vault", str(vault_path)])
        assert result.exit_code == 1

    def test_ingest_with_title(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        test_file = tmp_path / "test.md"
        test_file.write_text("Some content.")

        result = runner.invoke(app, [
            "ingest", str(test_file),
            "--vault", str(vault_path),
            "--title", "Custom Title",
        ])
        assert result.exit_code == 0
        assert "Custom Title" in result.output

    def test_ingest_multiple_files(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        f1 = tmp_path / "one.md"
        f1.write_text("# File One")
        f2 = tmp_path / "two.txt"
        f2.write_text("File two content")
        f3 = tmp_path / "three.md"
        f3.write_text("# File Three")

        result = runner.invoke(app, [
            "ingest", str(f1), str(f2), str(f3),
            "--vault", str(vault_path),
        ])
        assert result.exit_code == 0
        assert "3 file(s) ingested" in result.output

    def test_ingest_directory(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A")
        (docs_dir / "b.md").write_text("# B")
        (docs_dir / "c.txt").write_text("C content")

        result = runner.invoke(app, [
            "ingest", str(docs_dir),
            "--vault", str(vault_path),
        ])
        assert result.exit_code == 0
        assert "3 file(s) ingested" in result.output

    def test_ingest_directory_with_glob(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "a.md").write_text("# A")
        (docs_dir / "b.md").write_text("# B")
        (docs_dir / "c.txt").write_text("C content")

        result = runner.invoke(app, [
            "ingest", str(docs_dir),
            "--vault", str(vault_path),
            "--glob", "*.md",
        ])
        assert result.exit_code == 0
        assert "2 file(s) ingested" in result.output

    def test_ingest_directory_recursive(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        docs_dir = tmp_path / "docs"
        sub_dir = docs_dir / "sub"
        sub_dir.mkdir(parents=True)
        (docs_dir / "a.md").write_text("# A")
        (sub_dir / "b.md").write_text("# B")

        result = runner.invoke(app, [
            "ingest", str(docs_dir),
            "--vault", str(vault_path),
            "--glob", "*.md",
            "--recursive",
        ])
        assert result.exit_code == 0
        assert "2 file(s) ingested" in result.output


class TestStatusCommand:
    def test_status_empty_vault(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        result = runner.invoke(app, ["status", "--vault", str(vault_path)])
        assert result.exit_code == 0
        assert "Vault Status" in result.output

    def test_status_after_ingest(self, vault_path, tmp_path):
        runner.invoke(app, ["init", str(vault_path)])

        test_file = tmp_path / "test.md"
        test_file.write_text("# Test content")
        runner.invoke(app, ["ingest", str(test_file), "--vault", str(vault_path)])

        result = runner.invoke(app, ["status", "--vault", str(vault_path)])
        assert result.exit_code == 0


class TestHealthCommand:
    def test_health_empty_vault(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        result = runner.invoke(app, ["health", "--vault", str(vault_path)])
        assert result.exit_code == 0
        assert "Health Report" in result.output

    def test_health_save_report(self, vault_path):
        runner.invoke(app, ["init", str(vault_path)])
        result = runner.invoke(app, ["health", "--vault", str(vault_path), "--save"])
        assert result.exit_code == 0
        assert "Report saved" in result.output
        reports = list((vault_path / "system" / "health-reports").glob("*.md"))
        assert len(reports) == 1
