"""Compiler pipeline: summarize, extract entities/concepts, generate notes."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from secondbrain.database import Database, Source, Note, Entity
from secondbrain.llm.client import LLMClient
from secondbrain.vault.frontmatter import NoteFrontmatter, build_note
from secondbrain.vault.manager import VaultManager

DEFAULT_CONCURRENCY = 8


SUMMARIZE_SYSTEM = """You are compiling a local personal knowledge base.
Create a faithful summary of the source.
Do not add facts that are not present.
Return JSON only with these fields:
- title: string
- summary: string (2-4 sentences)
- key_ideas: list of strings
- entities: list of {name, type, aliases}
- tags: list of strings
- related_concepts: list of strings
- open_questions: list of strings"""

SUMMARIZE_PROMPT = """Summarize and extract structured information from this source text.

Source title: {title}
Source type: {source_type}

Text:
---
{text}
---

Return JSON only."""


BACKLINK_SYSTEM = """You propose Obsidian backlinks for a knowledge base note.
Only link to notes that are genuinely relevant.
Return JSON with a "links" array of {{target, reason, confidence}}."""

BACKLINK_PROMPT = """Given this note and existing notes in the vault, propose backlinks.

Note title: {title}
Note content:
---
{content}
---

Existing note titles:
{existing_titles}

Return JSON only."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    return "".join(c for c in slug if c.isalnum() or c == "-")[:80]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str, max_chars: int = 8000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


def _llm_summarize(
    source: Source,
    vault: VaultManager,
    db: Database,
    llm: LLMClient,
) -> dict[str, Any] | None:
    """IO-bound phase: call LLM to summarize. Safe to run concurrently."""
    raw_path = vault.root / source.raw_path
    if not raw_path.exists():
        db.log_activity(f"Skipping {source.id}: raw file missing", category="compile", level="warn")
        return None

    text = raw_path.read_text(encoding="utf-8", errors="replace")
    truncated = _truncate(text)

    prompt = SUMMARIZE_PROMPT.format(
        title=source.title or "Untitled",
        source_type=source.source_type,
        text=truncated,
    )
    resp = llm.generate(prompt, system=SUMMARIZE_SYSTEM)
    try:
        data = resp.as_json()
        return data
    except (json.JSONDecodeError, ValueError):
        db.log_activity(
            f"LLM returned invalid JSON for: {source.title or source.id}",
            category="compile", level="warn",
            detail=f"Raw response: {resp.text[:200]}",
        )
        return {
            "title": source.title or "Untitled",
            "summary": "Could not generate summary.",
            "key_ideas": [],
            "entities": [],
            "tags": [],
            "related_concepts": [],
            "open_questions": [],
        }


def _apply_compile_result(
    source: Source,
    data: dict[str, Any],
    vault: VaultManager,
    db: Database,
    propose_backlinks: bool = False,
    llm: LLMClient | None = None,
) -> list[Note]:
    """Write phase: create notes, register entities. Fast — no LLM calls unless propose_backlinks=True."""
    from secondbrain.indexes.search import index_note

    created_notes: list[Note] = []

    source_note = _create_source_note(source, data, vault, db)
    created_notes.append(source_note)
    index_note(db, vault, source_note)
    db.log_activity(
        f"Created source note: {source_note.title}",
        category="compile", level="info",
        detail=f"Path: {source_note.path}",
    )

    for ent in data.get("entities", []):
        _register_entity(ent, db)

    for concept_name in data.get("related_concepts", []):
        concept_note = _create_concept_note(concept_name, source, data, vault, db)
        if concept_note:
            created_notes.append(concept_note)
            index_note(db, vault, concept_note)

    if propose_backlinks and llm is not None:
        _propose_backlinks(source_note, vault, db, llm)

    return created_notes


def compile_source(
    source: Source,
    vault: VaultManager,
    db: Database,
    llm: LLMClient,
) -> list[Note]:
    db.log_activity(
        f"Compiling: {source.title or source.id}",
        category="compile", level="info",
        detail=f"Source type: {source.source_type}, ID: {source.id}",
    )

    data = _llm_summarize(source, vault, db, llm)
    if data is None:
        return []

    return _apply_compile_result(source, data, vault, db, propose_backlinks=True, llm=llm)


def _create_source_note(
    source: Source,
    data: dict[str, Any],
    vault: VaultManager,
    db: Database,
) -> Note:
    title = data.get("title", source.title or "Untitled")
    slug = _slugify(title)
    note_path = vault.compiled_path_for("sources", slug)

    now = _now_iso()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fm = NoteFrontmatter(
        title=title,
        note_type="source",
        source_ids=[source.id],
        tags=data.get("tags", []),
        confidence="high",
        created=today,
        updated=today,
    )

    body_parts = [f"# {title}\n"]
    body_parts.append("## Summary\n")
    body_parts.append(data.get("summary", "") + "\n")

    key_ideas = data.get("key_ideas", [])
    if key_ideas:
        body_parts.append("## Key Ideas\n")
        for idea in key_ideas:
            body_parts.append(f"- {idea}")
        body_parts.append("")

    related = data.get("related_concepts", [])
    if related:
        body_parts.append("## Related Notes\n")
        for r in related:
            body_parts.append(f"- [[{r}]]")
        body_parts.append("")

    questions = data.get("open_questions", [])
    if questions:
        body_parts.append("## Open Questions\n")
        for q in questions:
            body_parts.append(f"- {q}")
        body_parts.append("")

    body_parts.append("## Source\n")
    body_parts.append(f"- Source ID: `{source.id}`")
    body_parts.append(f"- Type: {source.source_type}")
    if source.original_uri:
        body_parts.append(f"- URI: {source.original_uri}")
    body_parts.append("")

    body = "\n".join(body_parts)
    content = build_note(fm, body)
    vault.write_note(note_path, content)

    note_id = f"note-{slug}"
    note = Note(
        id=note_id,
        path=str(note_path.relative_to(vault.root)),
        title=title,
        note_type="source",
        created_at=now,
        updated_at=now,
        content_hash=_content_hash(content),
    )
    db.add_note(note)
    db.add_note_source(note_id, source.id, "direct")

    return note


def _create_concept_note(
    concept_name: str,
    source: Source,
    data: dict[str, Any],
    vault: VaultManager,
    db: Database,
) -> Note | None:
    slug = _slugify(concept_name)
    note_path = vault.compiled_path_for("concepts", slug)

    # Don't overwrite existing concept notes
    if note_path.exists():
        return None

    now = _now_iso()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fm = NoteFrontmatter(
        title=concept_name,
        note_type="concept",
        source_ids=[source.id],
        tags=data.get("tags", []),
        confidence="medium",
        created=today,
        updated=today,
    )

    body = f"# {concept_name}\n\n"
    body += f"Concept extracted from [[{data.get('title', 'Untitled')}]].\n\n"
    body += "## Related Notes\n\n"
    body += f"- [[{data.get('title', 'Untitled')}]]\n"

    content = build_note(fm, body)
    vault.write_note(note_path, content)

    note_id = f"note-{slug}"
    note = Note(
        id=note_id,
        path=str(note_path.relative_to(vault.root)),
        title=concept_name,
        note_type="concept",
        created_at=now,
        updated_at=now,
        content_hash=_content_hash(content),
    )
    db.add_note(note)
    db.add_note_source(note_id, source.id, "extracted")

    return note


def _register_entity(ent: dict[str, Any], db: Database) -> None:
    name = ent.get("name", "")
    if not name:
        return
    entity_type = ent.get("type", "concept")
    slug = _slugify(name)
    entity_id = f"{entity_type}.{slug}"
    aliases = ent.get("aliases", [])

    entity = Entity(
        id=entity_id,
        name=name,
        entity_type=entity_type,
        aliases_json=json.dumps(aliases) if aliases else None,
    )
    db.add_entity(entity)


def _propose_backlinks(
    note: Note,
    vault: VaultManager,
    db: Database,
    llm: LLMClient,
) -> None:
    existing_notes = db.list_notes()
    if len(existing_notes) < 2:
        return

    existing_titles = "\n".join(
        f"- {n.title}" for n in existing_notes if n.id != note.id
    )

    note_path = vault.root / note.path
    content = note_path.read_text(encoding="utf-8") if note_path.exists() else ""

    prompt = BACKLINK_PROMPT.format(
        title=note.title,
        content=_truncate(content, 4000),
        existing_titles=existing_titles,
    )
    resp = llm.generate(prompt, system=BACKLINK_SYSTEM)
    try:
        data = resp.as_json()
    except (json.JSONDecodeError, ValueError):
        return

    for link in data.get("links", []):
        target_title = link.get("target", "")
        confidence = link.get("confidence", 0.5)
        # Find target note
        for existing in existing_notes:
            if existing.title.lower() == target_title.lower():
                db.add_link(note.id, existing.id, "backlink", confidence)
                break


_SENTINEL = None  # signals producer threads are done


def compile_all_pending(
    vault: VaultManager,
    db: Database,
    llm: LLMClient,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[Note]:
    uncompiled = db.get_uncompiled_sources()
    all_notes: list[Note] = []
    total = len(uncompiled)

    if total == 0:
        return all_notes

    db.log_activity(
        f"Starting compilation of {total} source(s) with concurrency={concurrency}",
        category="compile", level="info",
    )

    # Queue feeds LLM results to the writer as they complete
    result_queue: SimpleQueue[tuple[Source, dict[str, Any] | None, Exception | None] | None] = SimpleQueue()

    def _producer():
        """Fan out LLM calls, push results onto queue as they finish."""
        def _summarize_one(source: Source) -> tuple[Source, dict[str, Any] | None, Exception | None]:
            try:
                data = _llm_summarize(source, vault, db, llm)
                return (source, data, None)
            except Exception as e:
                return (source, None, e)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_summarize_one, src): src for src in uncompiled}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                src = futures[future]
                source, data, err = future.result()
                if err:
                    db.log_activity(
                        f"LLM failed {completed}/{total}: {src.title or src.id}",
                        category="compile", level="error",
                        detail=str(err),
                    )
                else:
                    db.log_activity(
                        f"LLM done {completed}/{total}: {src.title or src.id}",
                        category="compile", level="info",
                    )
                result_queue.put((source, data, err))
        result_queue.put(_SENTINEL)

    # Start producer in background thread
    producer_thread = threading.Thread(target=_producer, daemon=True)
    producer_thread.start()

    # Consumer: write notes as soon as each LLM result arrives
    written = 0
    while True:
        item = result_queue.get()
        if item is _SENTINEL:
            break

        source, data, err = item
        if err or data is None:
            continue

        try:
            notes = _apply_compile_result(source, data, vault, db)
            all_notes.extend(notes)
            job_id = f"compile-{source.id}"
            db.update_job_status(job_id, "completed")
            written += 1
            if written % 10 == 0 or written == total:
                db.log_activity(
                    f"Progress: {written}/{total} sources compiled ({len(all_notes)} notes)",
                    category="compile", level="info",
                )
        except Exception as e:
            db.log_activity(
                f"Write failed: {source.title or source.id}",
                category="compile", level="error",
                detail=str(e),
            )

    producer_thread.join()

    db.log_activity(
        f"Compilation finished: {len(all_notes)} notes from {total} sources",
        category="compile", level="info",
    )

    return all_notes
