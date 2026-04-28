"""Full-text search via SQLite FTS5, with fielded queries and faceting."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from secondbrain.database import Database, Note
from secondbrain.vault.manager import VaultManager
from secondbrain.vault.frontmatter import parse_frontmatter


@dataclass
class SearchHit:
    note: Note
    score: float
    excerpts: list[str]
    match_type: str
    matched_fields: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    note_type: str = ""
    confidence: str = ""


@dataclass
class Facets:
    note_types: list[tuple[str, int]] = field(default_factory=list)
    tags: list[tuple[str, int]] = field(default_factory=list)
    confidence: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[SearchHit]
    facets: Facets
    total: int
    query: str
    mode: str
    indexed_count: int = 0


def index_note(db: Database, vault: VaultManager, note: Note) -> None:
    """Index a single note into FTS5. Call after creating/updating a note."""
    note_path = vault.root / note.path
    if not note_path.exists():
        return
    content = note_path.read_text(encoding="utf-8", errors="replace")
    fm, body = parse_frontmatter(content)

    db.fts_upsert(
        note_id=note.id,
        title=note.title,
        body=body,
        tags=" ".join(fm.tags) if fm else "",
        note_type=fm.note_type if fm else note.note_type,
        confidence=fm.confidence if fm else "",
        source_ids=" ".join(fm.source_ids) if fm else "",
    )


def rebuild_index(db: Database, vault: VaultManager) -> int:
    """Full rebuild of the FTS5 index from all notes."""
    db.fts_rebuild()
    all_notes = db.list_notes()
    count = 0
    for note in all_notes:
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(content)
        db.fts_upsert(
            note_id=note.id,
            title=note.title,
            body=body,
            tags=" ".join(fm.tags) if fm else "",
            note_type=fm.note_type if fm else note.note_type,
            confidence=fm.confidence if fm else "",
            source_ids=" ".join(fm.source_ids) if fm else "",
        )
        count += 1
    return count


def ensure_index(db: Database, vault: VaultManager) -> None:
    """Rebuild index if it's empty but notes exist."""
    fts_count = db.fts_count()
    note_count = len(db.list_notes())
    if note_count > 0 and fts_count < note_count * 0.5:
        db.log_activity(
            f"FTS index stale ({fts_count}/{note_count}), rebuilding",
            category="search", level="info",
        )
        n = rebuild_index(db, vault)
        db.log_activity(f"FTS index rebuilt: {n} notes indexed", category="search", level="info")


def search(
    query: str,
    vault: VaultManager,
    db: Database,
    mode: str = "fulltext",
    fields: list[str] | None = None,
    filter_type: str | None = None,
    filter_tag: str | None = None,
    filter_confidence: str | None = None,
    max_results: int = 50,
    llm_base_url: str | None = None,
) -> SearchResult:
    """Unified search with FTS5, fielded queries, faceting."""

    ensure_index(db, vault)

    if mode == "semantic" and llm_base_url and query:
        hits = _semantic_search(query, vault, db, llm_base_url, max_results)
        if hits:
            facets = _compute_facets_from_hits(hits)
            return SearchResult(
                hits=hits, facets=facets, total=len(hits),
                query=query, mode="semantic", indexed_count=db.fts_count(),
            )
        # Fall through to fulltext

    if not query and not (filter_type or filter_tag or filter_confidence):
        return SearchResult(
            hits=[], facets=Facets(), total=0,
            query=query, mode="fulltext", indexed_count=db.fts_count(),
        )

    # Tag filter: search for tag in the tags field
    if filter_tag and query:
        # Combine user query with tag filter
        tag_clause = f'tags : "{filter_tag}"'
        fts_query = f'({query}) AND ({tag_clause})'
    elif filter_tag:
        fts_query = f'tags : "{filter_tag}"'
    else:
        fts_query = query

    rows = db.fts_search(
        fts_query,
        fields=fields,
        filter_type=filter_type,
        filter_confidence=filter_confidence,
        limit=max_results,
    ) if fts_query else []

    # Build hits
    hits = []
    type_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    conf_counter: Counter[str] = Counter()

    for row in rows:
        note = db.get_note(row["note_id"])
        if not note:
            continue

        row_tags = [t for t in row.get("tags", "").split() if t]
        note_type = row.get("note_type", "")
        confidence = row.get("confidence", "")

        type_counter[note_type] += 1
        conf_counter[confidence] += 1
        for t in row_tags:
            tag_counter[t] += 1

        # Determine matched fields from the query
        matched_fields = []
        if fields:
            matched_fields = list(fields)
        elif query:
            q_lower = query.lower()
            if q_lower in note.title.lower():
                matched_fields.append("title")
            if any(q_lower in t.lower() for t in row_tags):
                matched_fields.append("tags")
            if not matched_fields:
                matched_fields.append("body")

        snippet = row.get("snippet", "")
        excerpts = [snippet] if snippet else []

        hits.append(SearchHit(
            note=note,
            score=abs(row.get("rank", 0)),
            excerpts=excerpts,
            match_type="fulltext",
            matched_fields=matched_fields,
            tags=row_tags,
            note_type=note_type,
            confidence=confidence,
        ))

    facets = Facets(
        note_types=type_counter.most_common(20),
        tags=tag_counter.most_common(30),
        confidence=[(c, n) for c, n in conf_counter.most_common(10) if c],
    )

    return SearchResult(
        hits=hits, facets=facets, total=len(hits),
        query=query, mode="fulltext", indexed_count=db.fts_count(),
    )


def _compute_facets_from_hits(hits: list[SearchHit]) -> Facets:
    type_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    conf_counter: Counter[str] = Counter()
    for h in hits:
        type_counter[h.note_type] += 1
        conf_counter[h.confidence] += 1
        for t in h.tags:
            tag_counter[t] += 1
    return Facets(
        note_types=type_counter.most_common(20),
        tags=tag_counter.most_common(30),
        confidence=[(c, n) for c, n in conf_counter.most_common(10) if c],
    )


# --- Legacy compatibility ---

def fulltext_search(query, vault, db, max_results=30):
    result = search(query, vault, db, mode="fulltext", max_results=max_results)
    return result.hits


# --- Semantic search ---

def _semantic_search(
    query: str,
    vault: VaultManager,
    db: Database,
    llm_base_url: str,
    max_results: int = 20,
) -> list[SearchHit]:
    try:
        resp = httpx.post(
            f"{llm_base_url.rstrip('/')}/v1/embeddings",
            json={"input": query, "model": "default"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            return []

        query_embedding = resp.json()["data"][0]["embedding"]

        all_notes = db.list_notes()
        note_texts = []
        valid_notes = []
        for note in all_notes:
            note_path = vault.root / note.path
            if not note_path.exists():
                continue
            content = note_path.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(content)
            note_texts.append(body[:500])
            valid_notes.append((note, body, fm))

        if not note_texts:
            return []

        all_embeddings: list[list[float]] = []
        batch_size = 32
        for i in range(0, len(note_texts), batch_size):
            batch = note_texts[i:i + batch_size]
            resp = httpx.post(
                f"{llm_base_url.rstrip('/')}/v1/embeddings",
                json={"input": batch, "model": "default"},
                timeout=60.0,
            )
            if resp.status_code != 200:
                return []
            for item in resp.json()["data"]:
                all_embeddings.append(item["embedding"])

        hits = []
        terms = query.lower().split()
        for (note, body, fm), embedding in zip(valid_notes, all_embeddings):
            sim = _cosine_similarity(query_embedding, embedding)
            if sim > 0.3:
                hits.append(SearchHit(
                    note=note, score=sim, excerpts=[body[:200] + "..."],
                    match_type="semantic",
                    tags=fm.tags if fm else [],
                    note_type=fm.note_type if fm else note.note_type,
                    confidence=fm.confidence if fm else "",
                ))

        hits.sort(key=lambda x: -x.score)
        return hits[:max_results]

    except Exception:
        return []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
