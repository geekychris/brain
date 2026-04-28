"""VaultForge Web UI — FastAPI application."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from secondbrain.database import Database, LLMConfig
from secondbrain.llm.client import LlamaCppClient, create_client_from_config, LLMClient
from secondbrain.vault.manager import VaultManager
from secondbrain.vault.frontmatter import parse_frontmatter
from secondbrain.vault.registry import VaultRegistry

VAULT_PATH: Path | None = None

# Default config — seeded into DB on first run
DEFAULT_LLM_CONFIG = LLMConfig(
    id="spark-nemotron",
    name="Spark Nemotron 30B",
    backend_type="llamacpp",
    base_url="http://spark.local:30000",
    model="Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf",
    is_active=1,
)

app = FastAPI(title="VaultForge", description="Local LLM-powered second brain")

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


def _vault() -> VaultManager:
    assert VAULT_PATH is not None, "Vault path not configured"
    return VaultManager(VAULT_PATH)


def _db() -> Database:
    v = _vault()
    return Database(v.db_path)


def _llm() -> LLMClient:
    db = _db()
    config = db.get_active_llm_config()
    if config:
        return create_client_from_config(config)
    return LlamaCppClient(
        base_url=DEFAULT_LLM_CONFIG.base_url,
        model=DEFAULT_LLM_CONFIG.model,
    )


def _seed_default_config() -> None:
    """Ensure the default LLM config exists in the DB."""
    db = _db()
    db.init_schema()
    existing = db.list_llm_configs()
    if not existing:
        db.add_llm_config(DEFAULT_LLM_CONFIG)


_compile_lock = threading.Lock()
_compile_running = False


def _run_compile_background():
    global _compile_running
    from secondbrain.compiler.compile import compile_all_pending

    try:
        vault = _vault()
        db = _db()
        llm = _llm()
        compile_all_pending(vault, db, llm)
    except Exception as e:
        db = _db()
        db.log_activity(f"Compile failed: {e}", category="compile", level="error", detail=str(e))
    finally:
        with _compile_lock:
            _compile_running = False


@app.on_event("startup")
async def startup_auto_compile():
    """Seed default config and auto-start compilation if there are pending sources."""
    if VAULT_PATH is None:
        return
    _seed_default_config()
    db = _db()
    uncompiled = db.get_uncompiled_sources()
    if uncompiled:
        global _compile_running
        with _compile_lock:
            if _compile_running:
                return
            _compile_running = True
        db.log_activity(
            f"Auto-starting compile for {len(uncompiled)} pending source(s)",
            category="compile", level="info",
        )
        thread = threading.Thread(target=_run_compile_background, daemon=True)
        thread.start()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    db = _db()
    vault = _vault()
    sources = db.list_sources()
    notes = db.list_notes()
    entities = db.list_entities()
    pending_jobs = db.get_pending_jobs()
    uncompiled = db.get_uncompiled_sources()

    type_counts: dict[str, int] = {}
    for n in notes:
        type_counts[n.note_type] = type_counts.get(n.note_type, 0) + 1

    return templates.TemplateResponse("index.html", {
        "request": request,
        "total_sources": len(sources),
        "total_notes": len(notes),
        "total_entities": len(entities),
        "pending_jobs": len(pending_jobs),
        "uncompiled": len(uncompiled),
        "type_counts": type_counts,
        "recent_notes": notes[:10],
        "recent_sources": sources[:10],
    })


@app.get("/notes", response_class=HTMLResponse)
async def notes_list(request: Request, note_type: str | None = None):
    db = _db()
    notes = db.list_notes(note_type)
    return templates.TemplateResponse("notes.html", {
        "request": request,
        "notes": notes,
        "note_type": note_type,
    })


@app.get("/notes/{note_id}", response_class=HTMLResponse)
async def note_detail(request: Request, note_id: str):
    db = _db()
    vault = _vault()
    note = db.get_note(note_id)
    if not note:
        return HTMLResponse("<h1>Note not found</h1>", status_code=404)

    note_path = vault.root / note.path
    content = ""
    fm = None
    body = ""
    if note_path.exists():
        content = note_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(content)

    sources = db.get_note_sources(note_id)
    outbound = db.get_outbound_links(note_id)
    inbound = db.get_inbound_links(note_id)

    return templates.TemplateResponse("note_detail.html", {
        "request": request,
        "note": note,
        "frontmatter": fm,
        "body": body,
        "sources": sources,
        "outbound_links": outbound,
        "inbound_links": inbound,
    })


@app.get("/sources", response_class=HTMLResponse)
async def sources_list(request: Request):
    db = _db()
    sources = db.list_sources()
    return templates.TemplateResponse("sources.html", {
        "request": request,
        "sources": sources,
    })


@app.get("/ingest", response_class=HTMLResponse)
async def ingest_form(request: Request):
    return templates.TemplateResponse("ingest.html", {"request": request})


@app.post("/ingest/files")
async def ingest_files_endpoint(
    files: list[UploadFile] = File(...),
):
    from secondbrain.ingest.pipeline import ingest_file as do_ingest
    import tempfile

    vault = _vault()
    db = _db()
    ingested_ids = []

    for file in files:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        source = do_ingest(tmp_path, vault, db)
        tmp_path.unlink(missing_ok=True)
        ingested_ids.append(source.id)

    return RedirectResponse(url=f"/sources?ingested={len(ingested_ids)}", status_code=303)


@app.post("/ingest/url")
async def ingest_url_endpoint(url: str = Form(...)):
    from secondbrain.ingest.pipeline import ingest_url as do_ingest_url

    vault = _vault()
    db = _db()
    source = do_ingest_url(url, vault, db)
    return RedirectResponse(url=f"/sources?ingested={source.id}", status_code=303)


@app.post("/ingest/text")
async def ingest_text_endpoint(
    title: str = Form(...),
    text: str = Form(...),
):
    from secondbrain.ingest.pipeline import ingest_text as do_ingest_text

    vault = _vault()
    db = _db()
    source = do_ingest_text(text, title, vault, db)
    return RedirectResponse(url=f"/sources?ingested={source.id}", status_code=303)


@app.post("/compile")
async def compile_endpoint():
    global _compile_running
    db = _db()

    with _compile_lock:
        if _compile_running:
            db.log_activity("Compile already in progress, skipping", category="compile", level="warn")
            return RedirectResponse(url="/log?category=compile", status_code=303)
        _compile_running = True

    db.log_activity("Compile requested via web UI", category="compile", level="info")
    thread = threading.Thread(target=_run_compile_background, daemon=True)
    thread.start()
    return RedirectResponse(url="/log?category=compile", status_code=303)


@app.get("/compile/status")
async def compile_status():
    return JSONResponse({"running": _compile_running})


@app.get("/ask", response_class=HTMLResponse)
async def ask_form(request: Request):
    return templates.TemplateResponse("ask.html", {
        "request": request,
        "answer": None,
    })


@app.post("/ask", response_class=HTMLResponse)
async def ask_endpoint(request: Request, question: str = Form(...)):
    from secondbrain.query.engine import Answer, ask_vault, search_vault

    vault = _vault()
    db = _db()
    search_results = []
    answer = None
    error = None

    try:
        llm = _llm()
        answer = ask_vault(question, vault, db, llm)
        search_results = search_vault(question, vault, db)
    except Exception as e:
        error = str(e)
        # Still try to get search results even if LLM fails
        try:
            search_results = search_vault(question, vault, db)
        except Exception:
            pass

    return templates.TemplateResponse("ask.html", {
        "request": request,
        "question": question,
        "answer": answer,
        "search_results": search_results,
        "error": error,
    })


@app.get("/health", response_class=HTMLResponse)
async def health_endpoint(request: Request):
    from secondbrain.health.checks import run_health_check

    vault = _vault()
    db = _db()
    report = run_health_check(vault, db)

    return templates.TemplateResponse("health.html", {
        "request": request,
        "report": report,
        "report_md": report.to_markdown(),
    })


@app.get("/entities", response_class=HTMLResponse)
async def entities_list(request: Request, entity_type: str | None = None, q: str | None = None):
    db = _db()
    entities = db.list_entities(entity_type)
    if q:
        q_lower = q.lower()
        entities = [e for e in entities if q_lower in e.name.lower()]

    # Count types for filter tabs
    all_entities = db.list_entities()
    type_counts: dict[str, int] = {}
    for e in all_entities:
        t = e.entity_type.lower()
        type_counts[t] = type_counts.get(t, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:12]

    return templates.TemplateResponse("entities.html", {
        "request": request,
        "entities": entities,
        "entity_type": entity_type,
        "query": q or "",
        "top_types": top_types,
        "total_count": len(all_entities),
    })


@app.get("/entities/{entity_id}", response_class=HTMLResponse)
async def entity_detail(request: Request, entity_id: str):
    from secondbrain.indexes.graph import get_entity_detail
    vault = _vault()
    db = _db()
    detail = get_entity_detail(entity_id, vault, db)
    if not detail:
        return HTMLResponse("<h1>Entity not found</h1>", status_code=404)
    return templates.TemplateResponse("entity_detail.html", {
        "request": request,
        **detail,
    })


@app.get("/concepts", response_class=HTMLResponse)
async def concepts_page(request: Request, q: str | None = None):
    from secondbrain.indexes.graph import _get_cached_index
    vault = _vault()
    db = _db()
    concepts = _get_cached_index(vault, db)

    # Sort by mention count
    sorted_concepts = sorted(concepts.values(), key=lambda c: -c.mention_count)

    if q:
        q_lower = q.lower()
        sorted_concepts = [c for c in sorted_concepts if q_lower in c.title.lower()]

    return templates.TemplateResponse("concepts.html", {
        "request": request,
        "concepts": sorted_concepts[:200],
        "total": len(concepts),
        "query": q or "",
    })


@app.get("/concepts/{concept_title:path}", response_class=HTMLResponse)
async def concept_detail(request: Request, concept_title: str):
    from secondbrain.indexes.graph import get_concept_detail
    vault = _vault()
    db = _db()
    detail = get_concept_detail(concept_title, vault, db)
    if not detail:
        return HTMLResponse("<h1>Concept not found</h1>", status_code=404)
    return templates.TemplateResponse("concept_detail.html", {
        "request": request,
        **detail,
    })


@app.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request, focus: str = ""):
    return templates.TemplateResponse("graph.html", {"request": request, "focus": focus})


@app.get("/graph/data")
async def graph_data_api(
    min_mentions: int = 3,
    min_co_mentions: int = 2,
    max_nodes: int = 200,
    focus: str = "",
    depth: int = 2,
):
    from secondbrain.indexes.graph import build_graph_data, build_focused_graph, find_clusters
    vault = _vault()
    db = _db()

    if focus:
        graph = build_focused_graph(
            focus, vault, db,
            min_mentions=min_mentions, min_co_mentions=min_co_mentions,
            max_nodes=max_nodes, depth=depth,
        )
    else:
        graph = build_graph_data(
            vault, db,
            min_mentions=min_mentions, min_co_mentions=min_co_mentions,
            max_nodes=max_nodes,
        )

    clusters = find_clusters(graph)
    cluster_list = [
        {"id": i, "size": len(c), "labels": [n["label"] for n in c[:8]]}
        for i, c in enumerate(clusters) if len(c) >= 2
    ]

    return JSONResponse({
        "nodes": graph.nodes,
        "edges": graph.edges,
        "clusters": cluster_list,
    })


@app.post("/graph/summarize")
async def graph_summarize_api(request: Request):
    from secondbrain.indexes.graph import summarize_cluster
    body = await request.json()
    cluster_nodes = body.get("nodes", [])
    if not cluster_nodes:
        return JSONResponse({"error": "No nodes provided"}, status_code=400)

    vault = _vault()
    db = _db()
    llm = _llm()
    summary = summarize_cluster(cluster_nodes, vault, db, llm)
    return JSONResponse({"summary": summary})


@app.get("/log", response_class=HTMLResponse)
async def log_page(request: Request, category: str | None = None):
    db = _db()
    entries = db.get_activity_log(limit=200, category=category)
    return templates.TemplateResponse("log.html", {
        "request": request,
        "entries": entries,
        "category": category,
        "compile_running": _compile_running,
    })


@app.get("/log/entries")
async def log_entries_api(category: str | None = None, since_id: int = 0):
    db = _db()
    entries = db.get_activity_log(limit=50, category=category)
    # Filter to only entries newer than since_id
    if since_id:
        entries = [e for e in entries if e["id"] > since_id]
    return JSONResponse({
        "entries": entries,
        "compile_running": _compile_running,
    })


# --- LLM Settings ---

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    db = _db()
    configs = db.list_llm_configs()
    active = db.get_active_llm_config()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "configs": configs,
        "active_id": active.id if active else None,
    })


@app.post("/settings/config")
async def save_config(
    config_id: str = Form(""),
    name: str = Form(...),
    backend_type: str = Form(...),
    base_url: str = Form(...),
    model: str = Form(...),
):
    db = _db()
    if not config_id:
        import hashlib
        config_id = hashlib.md5(f"{name}{base_url}{model}".encode()).hexdigest()[:12]

    existing = db.get_llm_config(config_id)
    config = LLMConfig(
        id=config_id,
        name=name,
        backend_type=backend_type,
        base_url=base_url,
        model=model,
        is_active=existing.is_active if existing else 0,
        created_at=existing.created_at if existing else "",
    )
    db.add_llm_config(config)
    db.log_activity(f"Saved LLM config: {name}", category="settings", level="info",
                    detail=f"{backend_type} @ {base_url}, model: {model}")
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/config/{config_id}/activate")
async def activate_config(config_id: str):
    db = _db()
    config = db.get_llm_config(config_id)
    if config:
        db.activate_llm_config(config_id)
        db.log_activity(f"Activated LLM config: {config.name}", category="settings", level="info",
                        detail=f"{config.backend_type} @ {config.base_url}, model: {config.model}")
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/config/{config_id}/delete")
async def delete_config(config_id: str):
    db = _db()
    config = db.get_llm_config(config_id)
    if config:
        db.delete_llm_config(config_id)
        db.log_activity(f"Deleted LLM config: {config.name}", category="settings", level="info")
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/probe")
async def probe_endpoint_api(
    base_url: str = Form(...),
    backend_type: str = Form("llamacpp"),
):
    from secondbrain.llm.client import probe_endpoint
    result = probe_endpoint(base_url, backend_type)
    return JSONResponse(result)


@app.post("/settings/test")
async def test_config(
    base_url: str = Form(...),
    backend_type: str = Form("llamacpp"),
    model: str = Form(...),
):
    """Quick test: send a trivial prompt and check we get a response."""
    try:
        config = LLMConfig(id="test", name="test", backend_type=backend_type,
                           base_url=base_url, model=model)
        client = create_client_from_config(config)
        resp = client.generate("Say hello in one word.", temperature=0.1)
        return JSONResponse({"success": True, "response": resp.text[:200], "model": resp.model})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# --- File Viewer ---

@app.get("/view/{file_path:path}", response_class=HTMLResponse)
async def view_file(request: Request, file_path: str):
    """View a file from the vault — renders markdown, embeds PDFs."""
    vault = _vault()
    db = _db()
    full_path = vault.root / file_path

    if not full_path.exists() or not full_path.is_file():
        return HTMLResponse("<h1>File not found</h1>", status_code=404)

    try:
        full_path.resolve().relative_to(vault.root.resolve())
    except ValueError:
        return HTMLResponse("<h1>Access denied</h1>", status_code=403)

    # Look up note and annotation
    note = db.get_note_by_path(file_path)
    annotation = db.get_annotation(note.id) if note else None

    suffix = full_path.suffix.lower()
    common_ctx = {
        "request": request,
        "file_path": file_path,
        "note": note,
        "annotation": annotation,
        "all_labels": db.get_all_labels(),
        "all_user_tags": db.get_all_user_tags(),
    }

    if suffix == ".pdf":
        return templates.TemplateResponse("viewer_pdf.html", {
            **common_ctx,
            "title": full_path.stem,
        })
    elif suffix in (".md", ".markdown"):
        import markdown
        raw = full_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(raw)
        html_content = markdown.markdown(
            body, extensions=["tables", "fenced_code", "codehilite", "toc"]
        )
        import re
        def _wikilink_to_html(m):
            title = m.group(1)
            return f'<a href="/concepts/{title}" class="wikilink">[[{title}]]</a>'
        html_content = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', _wikilink_to_html, html_content)

        return templates.TemplateResponse("viewer_md.html", {
            **common_ctx,
            "title": fm.title if fm else full_path.stem,
            "frontmatter": fm,
            "html_content": html_content,
            "raw_content": raw,
        })
    else:
        raw = full_path.read_text(encoding="utf-8", errors="replace")
        return templates.TemplateResponse("viewer_text.html", {
            **common_ctx,
            "title": full_path.name,
            "content": raw,
        })


@app.get("/raw/{file_path:path}")
async def serve_raw_file(file_path: str):
    """Serve a raw file from the vault (for PDF embedding etc)."""
    vault = _vault()
    full_path = vault.root / file_path
    if not full_path.exists() or not full_path.is_file():
        return HTMLResponse("Not found", status_code=404)
    try:
        full_path.resolve().relative_to(vault.root.resolve())
    except ValueError:
        return HTMLResponse("Access denied", status_code=403)
    return FileResponse(full_path)


# --- Annotations ---

@app.post("/api/note/{note_id}/star")
async def toggle_star(note_id: str):
    db = _db()
    starred = db.toggle_star(note_id)
    return JSONResponse({"starred": starred})


@app.post("/api/note/{note_id}/labels")
async def set_labels(note_id: str, request: Request):
    body = await request.json()
    db = _db()
    db.set_labels(note_id, body.get("labels", []))
    return JSONResponse({"ok": True})


@app.post("/api/note/{note_id}/user_tags")
async def set_user_tags(note_id: str, request: Request):
    body = await request.json()
    db = _db()
    db.set_user_tags(note_id, body.get("user_tags", []))
    return JSONResponse({"ok": True})


@app.post("/api/note/{note_id}/summarize")
async def summarize_note(note_id: str):
    db = _db()
    vault = _vault()
    llm = _llm()

    note = db.get_note(note_id)
    if not note:
        return JSONResponse({"error": "Note not found"}, status_code=404)

    note_path = vault.root / note.path
    if not note_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    content = note_path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(content)

    prompt = f"""Summarize this note concisely (3-5 sentences). Capture the key points and main ideas.

Title: {note.title}

Content:
---
{body[:6000]}
---

Write a clear, concise summary."""

    resp = llm.generate(prompt, system="You are summarizing a note from a personal knowledge base. Be faithful to the content.", temperature=0.3)
    summary = resp.text.strip()

    db.set_summary(note_id, summary)
    return JSONResponse({"summary": summary})


# --- Search ---

@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = "",
    mode: str = "fulltext",
    field: list[str] = Query(default=[]),
    type: str = "",
    tag: str = "",
    confidence: str = "",
    starred: str = "",
    label: str = "",
    user_tag: str = "",
):
    from secondbrain.indexes.search import search as do_search

    vault = _vault()
    db = _db()

    fields = field if field else None
    llm_url = None
    if mode == "semantic":
        config = db.get_active_llm_config()
        llm_url = config.base_url if config else None

    has_filters = q or type or tag or confidence or starred or label or user_tag
    result = do_search(
        query=q, vault=vault, db=db, mode=mode,
        fields=fields,
        filter_type=type or None,
        filter_tag=tag or None,
        filter_confidence=confidence or None,
        llm_base_url=llm_url,
    ) if has_filters else None

    # Post-filter by annotation criteria
    if result and (starred or label or user_tag):
        filtered_hits = []
        for hit in result.hits:
            ann = db.get_annotation(hit.note.id)
            if starred and (not ann or not ann.starred):
                continue
            if label and (not ann or label not in ann.labels):
                continue
            if user_tag and (not ann or user_tag not in ann.user_tags):
                continue
            filtered_hits.append(hit)
        result.hits = filtered_hits
        result.total = len(filtered_hits)

    # Add annotation facets
    all_labels = db.get_all_labels()
    all_user_tags = db.get_all_user_tags()
    starred_count = len(db.get_starred_notes())

    return templates.TemplateResponse("search.html", {
        "request": request,
        "query": q,
        "mode": mode,
        "active_fields": fields or ["title", "body", "tags"],
        "filter_type": type,
        "filter_tag": tag,
        "filter_confidence": confidence,
        "filter_starred": starred,
        "filter_label": label,
        "filter_user_tag": user_tag,
        "result": result,
        "all_labels": all_labels,
        "all_user_tags": all_user_tags,
        "starred_count": starred_count,
    })


@app.post("/search/rebuild")
async def rebuild_search_index():
    from secondbrain.indexes.search import rebuild_index
    vault = _vault()
    db = _db()
    db.log_activity("FTS index rebuild requested", category="search", level="info")
    count = rebuild_index(db, vault)
    db.log_activity(f"FTS index rebuilt: {count} notes", category="search", level="info")
    return RedirectResponse(url="/search?q=&rebuilt=1", status_code=303)


# --- Multi-Vault ---

_registry: VaultRegistry | None = None


def _get_registry() -> VaultRegistry:
    global _registry
    if _registry is None:
        _registry = VaultRegistry()
    return _registry


@app.get("/vaults", response_class=HTMLResponse)
async def vaults_page(request: Request):
    registry = _get_registry()
    vaults = registry.list_vaults()
    active = registry.get_active()
    return templates.TemplateResponse("vaults.html", {
        "request": request,
        "vaults": vaults,
        "active_id": active.id if active else None,
        "current_vault": str(VAULT_PATH) if VAULT_PATH else None,
    })


@app.post("/vaults/register")
async def register_vault(
    vault_id: str = Form(""),
    name: str = Form(...),
    path: str = Form(...),
):
    import hashlib
    registry = _get_registry()
    if not vault_id:
        vault_id = hashlib.md5(path.encode()).hexdigest()[:12]

    vault_path = Path(path).expanduser().resolve()
    if not vault_path.exists():
        return RedirectResponse(url="/vaults?error=path_not_found", status_code=303)

    registry.register(vault_id, name, str(vault_path))
    return RedirectResponse(url="/vaults", status_code=303)


@app.post("/vaults/{vault_id}/activate")
async def activate_vault(vault_id: str):
    global VAULT_PATH
    registry = _get_registry()
    entry = registry.get_vault(vault_id)
    if entry:
        registry.activate(vault_id)
        VAULT_PATH = Path(entry.path)
        from secondbrain.indexes.graph import invalidate_cache
        invalidate_cache(_vault())
    return RedirectResponse(url="/vaults", status_code=303)


@app.post("/vaults/{vault_id}/remove")
async def remove_vault(vault_id: str):
    registry = _get_registry()
    registry.remove(vault_id)
    return RedirectResponse(url="/vaults", status_code=303)
