"""Ingestion pipeline: detect type, extract text, store raw, create source record."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from secondbrain.database import Database, Source, Job
from secondbrain.vault.manager import VaultManager


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:80]


def detect_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    type_map = {
        ".pdf": "pdf",
        ".md": "markdown",
        ".txt": "text",
        ".html": "html",
        ".htm": "html",
        ".csv": "csv",
        ".json": "json",
        ".py": "code",
        ".js": "code",
        ".ts": "code",
        ".go": "code",
        ".rs": "code",
        ".java": "code",
    }
    return type_map.get(suffix, "text")


def extract_text_from_file(path: Path) -> str:
    source_type = detect_source_type(path)

    if source_type == "pdf":
        return _extract_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n\n".join(pages)
    except ImportError:
        return f"[PDF extraction requires PyMuPDF: {path.name}]"


def extract_text_from_url(url: str) -> tuple[str, str]:
    resp = httpx.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Try article or main content first
    main = soup.find("article") or soup.find("main") or soup.find("body")
    text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

    return title, text


def ingest_file(
    file_path: Path,
    vault: VaultManager,
    db: Database,
    title: str | None = None,
) -> Source:
    content = file_path.read_bytes()
    content_hash = _content_hash(content)

    existing = db.get_source_by_hash(content_hash)
    if existing:
        db.log_activity(f"Skipped duplicate: {file_path.name}", category="ingest", level="info",
                        detail=f"Content hash {content_hash[:12]} already exists as {existing.id}")
        return existing

    now = datetime.now(timezone.utc)
    year = now.strftime("%Y")
    month = now.strftime("%m")
    source_type = detect_source_type(file_path)

    db.log_activity(f"Ingesting file: {file_path.name}", category="ingest", level="info",
                    detail=f"Type: {source_type}, Size: {len(content)} bytes")

    raw_dest = vault.raw_path_for(year, month, file_path.name)
    # Avoid overwriting if file already exists with different content
    if raw_dest.exists():
        stem = raw_dest.stem
        suffix = raw_dest.suffix
        raw_dest = raw_dest.with_name(f"{stem}-{content_hash[:8]}{suffix}")
    shutil.copy2(file_path, raw_dest)

    source_id = f"source-{content_hash[:12]}"
    source = Source(
        id=source_id,
        source_type=source_type,
        original_uri=str(file_path.resolve()),
        raw_path=str(raw_dest.relative_to(vault.root)),
        content_hash=content_hash,
        imported_at=_now_iso(),
        title=title or file_path.stem,
    )
    db.add_source(source)

    # Queue compile job
    job = Job(
        id=f"compile-{source_id}",
        job_type="compile",
        status="pending",
        input_json=json.dumps({"source_id": source_id}),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    db.add_job(job)

    return source


def ingest_url(
    url: str,
    vault: VaultManager,
    db: Database,
) -> Source:
    title, text = extract_text_from_url(url)
    content = text.encode("utf-8")
    content_hash = _content_hash(content)

    existing = db.get_source_by_hash(content_hash)
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    year = now.strftime("%Y")
    month = now.strftime("%m")

    parsed = urlparse(url)
    slug = _slugify(parsed.netloc + "-" + parsed.path.rstrip("/").split("/")[-1])
    filename = f"{slug}.html"

    raw_dest = vault.raw_path_for(year, month, filename)
    raw_dest.write_text(text, encoding="utf-8")

    source_id = f"source-{content_hash[:12]}"
    source = Source(
        id=source_id,
        source_type="url",
        original_uri=url,
        raw_path=str(raw_dest.relative_to(vault.root)),
        content_hash=content_hash,
        imported_at=_now_iso(),
        title=title or slug,
    )
    db.add_source(source)

    job = Job(
        id=f"compile-{source_id}",
        job_type="compile",
        status="pending",
        input_json=json.dumps({"source_id": source_id}),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    db.add_job(job)

    return source


def ingest_text(
    text: str,
    title: str,
    vault: VaultManager,
    db: Database,
) -> Source:
    content = text.encode("utf-8")
    content_hash = _content_hash(content)

    existing = db.get_source_by_hash(content_hash)
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    year = now.strftime("%Y")
    month = now.strftime("%m")

    slug = _slugify(title)
    filename = f"{slug}.md"

    raw_dest = vault.raw_path_for(year, month, filename)
    raw_dest.write_text(text, encoding="utf-8")

    source_id = f"source-{content_hash[:12]}"
    source = Source(
        id=source_id,
        source_type="text",
        original_uri=None,
        raw_path=str(raw_dest.relative_to(vault.root)),
        content_hash=content_hash,
        imported_at=_now_iso(),
        title=title,
    )
    db.add_source(source)

    job = Job(
        id=f"compile-{source_id}",
        job_type="compile",
        status="pending",
        input_json=json.dumps({"source_id": source_id}),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    db.add_job(job)

    return source
