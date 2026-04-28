"""VaultForge CLI — secondbrain command."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from secondbrain.database import Database
from secondbrain.vault.manager import VaultManager

app = typer.Typer(name="secondbrain", help="VaultForge: Local LLM-powered second brain")
console = Console()


def _get_vault(vault_path: Path) -> VaultManager:
    return VaultManager(vault_path)


def _get_db(vault: VaultManager) -> Database:
    db = Database(vault.db_path)
    return db


def _get_llm(model: str | None = None, vault_path: Path | None = None) -> "LLMClient":
    from secondbrain.llm.client import LlamaCppClient, create_client_from_config
    # Try to load active config from DB
    if vault_path:
        v = _get_vault(vault_path)
        db = Database(v.db_path)
        config = db.get_active_llm_config()
        if config:
            if model:
                config.model = model
            return create_client_from_config(config)
    return LlamaCppClient(
        base_url="http://spark.local:30000",
        model=model or "Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf",
    )


@app.command()
def init(
    vault_path: Path = typer.Argument(..., help="Path to create the vault"),
) -> None:
    """Initialize a new VaultForge vault."""
    vault = _get_vault(vault_path)
    vault.init()

    db = _get_db(vault)
    db.init_schema()

    console.print(f"[green]Vault initialized at {vault_path}[/green]")
    console.print("Directory structure created:")
    console.print("  inbox/  — drop files here for ingestion")
    console.print("  raw/    — archived raw sources")
    console.print("  compiled/ — generated Obsidian-compatible notes")
    console.print("  daily/  — daily journal notes")
    console.print("  system/ — metadata, schemas, health reports")


@app.command()
def ingest(
    file_paths: list[Path] = typer.Argument(..., help="Files or directories to ingest"),
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Override title (single file only)"),
    glob_pattern: Optional[str] = typer.Option(None, "--glob", "-g", help="Glob pattern for directories (e.g. '*.pdf')"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Recurse into subdirectories"),
) -> None:
    """Ingest one or more files or directories into the vault."""
    from secondbrain.ingest.pipeline import ingest_file

    vault = _get_vault(vault_path)
    db = _get_db(vault)

    # Resolve all paths, expanding directories
    resolved: list[Path] = []
    for p in file_paths:
        if not p.exists():
            console.print(f"[red]Not found: {p}[/red]")
            continue
        if p.is_dir():
            pattern = glob_pattern or "*"
            glob_fn = p.rglob if recursive else p.glob
            resolved.extend(f for f in sorted(glob_fn(pattern)) if f.is_file())
        else:
            resolved.append(p)

    if not resolved:
        console.print("[red]No files to ingest.[/red]")
        raise typer.Exit(1)

    if title and len(resolved) > 1:
        console.print("[yellow]--title ignored when ingesting multiple files.[/yellow]")
        title = None

    ingested = 0
    skipped = 0
    for fp in resolved:
        source = ingest_file(fp, vault, db, title=title)
        if source:
            ingested += 1
            console.print(f"  [green]Ingested:[/green] {source.title} ({source.source_type})")
        else:
            skipped += 1

    console.print(f"\n[green]{ingested} file(s) ingested[/green]", end="")
    if skipped:
        console.print(f", [yellow]{skipped} skipped (duplicates)[/yellow]")
    else:
        console.print()
    console.print("Compile jobs queued.")


@app.command(name="ingest-url")
def ingest_url_cmd(
    url: str = typer.Argument(..., help="URL to ingest"),
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
) -> None:
    """Ingest a web page into the vault."""
    from secondbrain.ingest.pipeline import ingest_url

    vault = _get_vault(vault_path)
    db = _get_db(vault)

    source = ingest_url(url, vault, db)
    console.print(f"[green]Ingested URL:[/green] {source.title}")
    console.print(f"  Source ID: {source.id}")
    console.print(f"  Raw path: {source.raw_path}")
    console.print("  Compile job queued.")


@app.command()
def compile(
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model name"),
    concurrency: int = typer.Option(8, "--concurrency", "-j", help="Number of concurrent LLM calls"),
) -> None:
    """Compile all pending sources into Obsidian notes."""
    from secondbrain.compiler.compile import compile_all_pending

    vault = _get_vault(vault_path)
    db = _get_db(vault)
    llm = _get_llm(model, vault_path)

    notes = compile_all_pending(vault, db, llm, concurrency=concurrency)
    if not notes:
        console.print("[yellow]No pending sources to compile.[/yellow]")
        return

    console.print(f"[green]Compiled {len(notes)} notes:[/green]")
    for note in notes:
        console.print(f"  [{note.note_type}] {note.title} → {note.path}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask the vault"),
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model name"),
) -> None:
    """Ask a question and get a source-grounded answer from the vault."""
    from secondbrain.query.engine import ask_vault

    vault = _get_vault(vault_path)
    db = _get_db(vault)
    llm = _get_llm(model, vault_path)

    answer = ask_vault(question, vault, db, llm)

    console.print(f"\n[bold]{answer.text}[/bold]\n")
    console.print(f"Confidence: {answer.confidence}")
    console.print(f"Answer type: {answer.answer_type}")
    if answer.sources:
        console.print("Sources:")
        for s in answer.sources:
            console.print(f"  - {s}")


@app.command()
def health(
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
    save: bool = typer.Option(False, "--save", help="Save report to system/health-reports/"),
) -> None:
    """Run vault health checks."""
    from secondbrain.health.checks import run_health_check

    vault = _get_vault(vault_path)
    db = _get_db(vault)

    report = run_health_check(vault, db)
    md = report.to_markdown()
    console.print(md)

    if save:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = vault.system_dir / "health-reports" / f"{today}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8")
        console.print(f"\n[green]Report saved to {report_path}[/green]")


@app.command()
def status(
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
) -> None:
    """Show vault status summary."""
    vault = _get_vault(vault_path)
    db = _get_db(vault)

    sources = db.list_sources()
    notes = db.list_notes()
    entities = db.list_entities()
    pending_jobs = db.get_pending_jobs()

    table = Table(title="Vault Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green")

    table.add_row("Total sources", str(len(sources)))
    table.add_row("Total notes", str(len(notes)))
    table.add_row("Entities", str(len(entities)))
    table.add_row("Pending jobs", str(len(pending_jobs)))

    fts_count = db.fts_count()
    table.add_row("FTS indexed", str(fts_count))

    # Note type breakdown
    type_counts: dict[str, int] = {}
    for n in notes:
        type_counts[n.note_type] = type_counts.get(n.note_type, 0) + 1
    for nt, count in sorted(type_counts.items()):
        table.add_row(f"  {nt} notes", str(count))

    console.print(table)


@app.command()
def serve(
    vault_path: Path = typer.Option(".", "--vault", "-v", help="Vault path"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
) -> None:
    """Start the VaultForge web UI."""
    import uvicorn
    from secondbrain.ui.app import app as web_app, VAULT_PATH
    import secondbrain.ui.app as ui_module

    vault = _get_vault(vault_path)
    if not vault.db_path.exists():
        console.print("[red]Vault not initialized. Run: secondbrain init <path>[/red]")
        raise typer.Exit(1)

    ui_module.VAULT_PATH = vault_path.resolve()

    # Show active LLM config
    db = _get_db(vault)
    llm_config = db.get_active_llm_config()
    llm_info = f"{llm_config.base_url} ({llm_config.name})" if llm_config else "None configured — go to /settings"

    console.print(f"[green]VaultForge UI starting...[/green]")
    console.print(f"  Vault: {vault_path.resolve()}")
    console.print(f"  URL:   http://{host}:{port}")
    console.print(f"  LLM:   {llm_info}")
    uvicorn.run(web_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
