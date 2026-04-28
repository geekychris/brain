"""SQLite database layer for VaultForge metadata."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    original_uri TEXT,
    raw_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    title TEXT,
    author TEXT,
    created_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    note_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_sources (
    note_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_type TEXT,
    PRIMARY KEY (note_id, source_id),
    FOREIGN KEY (note_id) REFERENCES notes(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS links (
    from_note_id TEXT NOT NULL,
    to_note_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY (from_note_id, to_note_id, link_type),
    FOREIGN KEY (from_note_id) REFERENCES notes(id)
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases_json TEXT,
    canonical_note_id TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_configs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    backend_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_annotations (
    note_id TEXT PRIMARY KEY,
    starred INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '',
    user_tags TEXT NOT NULL DEFAULT '',
    summary TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(id)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    title,
    body,
    tags,
    note_type UNINDEXED,
    confidence UNINDEXED,
    source_ids UNINDEXED,
    tokenize='porter unicode61'
);

CREATE INDEX IF NOT EXISTS idx_activity_log_timestamp ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_sources_content_hash ON sources(content_hash);
CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(note_type);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


@dataclass
class Source:
    id: str
    source_type: str
    raw_path: str
    content_hash: str
    imported_at: str
    original_uri: str | None = None
    title: str | None = None
    author: str | None = None
    created_at: str | None = None
    metadata_json: str | None = None


@dataclass
class Note:
    id: str
    path: str
    title: str
    note_type: str
    created_at: str
    updated_at: str
    content_hash: str


@dataclass
class Entity:
    id: str
    name: str
    entity_type: str
    aliases_json: str | None = None
    canonical_note_id: str | None = None


@dataclass
class NoteAnnotation:
    note_id: str
    starred: int = 0
    labels: str = ""  # comma-separated
    user_tags: str = ""  # comma-separated
    summary: str | None = None
    updated_at: str = ""


@dataclass
class LLMConfig:
    id: str
    name: str
    backend_type: str  # "llamacpp" or "ollama"
    base_url: str
    model: str
    is_active: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Job:
    id: str
    job_type: str
    status: str
    input_json: str
    created_at: str
    updated_at: str
    result_json: str | None = None


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def add_source(self, source: Source) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sources
                   (id, source_type, original_uri, raw_path, content_hash,
                    imported_at, title, author, created_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.id, source.source_type, source.original_uri,
                    source.raw_path, source.content_hash, source.imported_at,
                    source.title, source.author, source.created_at,
                    source.metadata_json,
                ),
            )

    def get_source(self, source_id: str) -> Source | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                return None
            return Source(**dict(row))

    def get_source_by_hash(self, content_hash: str) -> Source | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if row is None:
                return None
            return Source(**dict(row))

    def list_sources(self, source_type: str | None = None) -> list[Source]:
        with self.connection() as conn:
            if source_type:
                rows = conn.execute(
                    "SELECT * FROM sources WHERE source_type = ? ORDER BY imported_at DESC",
                    (source_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sources ORDER BY imported_at DESC"
                ).fetchall()
            return [Source(**dict(r)) for r in rows]

    def add_note(self, note: Note) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO notes
                   (id, path, title, note_type, created_at, updated_at, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    note.id, note.path, note.title, note.note_type,
                    note.created_at, note.updated_at, note.content_hash,
                ),
            )

    def get_note(self, note_id: str) -> Note | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if row is None:
                return None
            return Note(**dict(row))

    def get_note_by_path(self, path: str) -> Note | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                return None
            return Note(**dict(row))

    def list_notes(self, note_type: str | None = None) -> list[Note]:
        with self.connection() as conn:
            if note_type:
                rows = conn.execute(
                    "SELECT * FROM notes WHERE note_type = ? ORDER BY updated_at DESC",
                    (note_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM notes ORDER BY updated_at DESC"
                ).fetchall()
            return [Note(**dict(r)) for r in rows]

    def add_note_source(self, note_id: str, source_id: str, evidence_type: str = "derived") -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO note_sources (note_id, source_id, evidence_type) VALUES (?, ?, ?)",
                (note_id, source_id, evidence_type),
            )

    def get_note_sources(self, note_id: str) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT source_id FROM note_sources WHERE note_id = ?", (note_id,)
            ).fetchall()
            return [r["source_id"] for r in rows]

    def add_link(self, from_note_id: str, to_note_id: str, link_type: str = "backlink", confidence: float = 1.0) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO links (from_note_id, to_note_id, link_type, confidence) VALUES (?, ?, ?, ?)",
                (from_note_id, to_note_id, link_type, confidence),
            )

    def get_outbound_links(self, note_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT to_note_id, link_type, confidence FROM links WHERE from_note_id = ?",
                (note_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_inbound_links(self, note_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT from_note_id, link_type, confidence FROM links WHERE to_note_id = ?",
                (note_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def add_entity(self, entity: Entity) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entities
                   (id, name, entity_type, aliases_json, canonical_note_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity.id, entity.name, entity.entity_type,
                 entity.aliases_json, entity.canonical_note_id),
            )

    def get_entity(self, entity_id: str) -> Entity | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                return None
            return Entity(**dict(row))

    def list_entities(self, entity_type: str | None = None) -> list[Entity]:
        with self.connection() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM entities WHERE entity_type = ?", (entity_type,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM entities").fetchall()
            return [Entity(**dict(r)) for r in rows]

    def add_job(self, job: Job) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, job_type, status, input_json, result_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (job.id, job.job_type, job.status, job.input_json,
                 job.result_json, job.created_at, job.updated_at),
            )

    def get_pending_jobs(self, job_type: str | None = None) -> list[Job]:
        with self.connection() as conn:
            if job_type:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'pending' AND job_type = ? ORDER BY created_at",
                    (job_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at"
                ).fetchall()
            return [Job(**dict(r)) for r in rows]

    def update_job_status(self, job_id: str, status: str, result_json: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            if result_json is not None:
                conn.execute(
                    "UPDATE jobs SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                    (status, result_json, now, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, job_id),
                )

    def search_notes_fulltext(self, query: str) -> list[Note]:
        with self.connection() as conn:
            pattern = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM notes WHERE title LIKE ? ORDER BY updated_at DESC",
                (pattern,),
            ).fetchall()
            return [Note(**dict(r)) for r in rows]

    def get_uncompiled_sources(self) -> list[Source]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT s.* FROM sources s
                   LEFT JOIN note_sources ns ON s.id = ns.source_id
                   WHERE ns.source_id IS NULL
                   ORDER BY s.imported_at""",
            ).fetchall()
            return [Source(**dict(r)) for r in rows]

    def log_activity(
        self,
        message: str,
        category: str = "general",
        level: str = "info",
        detail: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO activity_log (timestamp, level, category, message, detail) VALUES (?, ?, ?, ?, ?)",
                (now, level, category, message, detail),
            )

    def get_activity_log(self, limit: int = 100, category: str | None = None) -> list[dict]:
        with self.connection() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM activity_log WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # --- LLM Config methods ---

    def add_llm_config(self, config: LLMConfig) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not config.created_at:
            config.created_at = now
        config.updated_at = now
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO llm_configs
                   (id, name, backend_type, base_url, model, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (config.id, config.name, config.backend_type, config.base_url,
                 config.model, config.is_active, config.created_at, config.updated_at),
            )

    def get_llm_config(self, config_id: str) -> LLMConfig | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM llm_configs WHERE id = ?", (config_id,)).fetchone()
            if row is None:
                return None
            return LLMConfig(**dict(row))

    def list_llm_configs(self) -> list[LLMConfig]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM llm_configs ORDER BY name").fetchall()
            return [LLMConfig(**dict(r)) for r in rows]

    def get_active_llm_config(self) -> LLMConfig | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM llm_configs WHERE is_active = 1").fetchone()
            if row is None:
                return None
            return LLMConfig(**dict(row))

    def activate_llm_config(self, config_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE llm_configs SET is_active = 0, updated_at = ?",
                         (datetime.now(timezone.utc).isoformat(),))
            conn.execute("UPDATE llm_configs SET is_active = 1, updated_at = ? WHERE id = ?",
                         (datetime.now(timezone.utc).isoformat(), config_id))

    def delete_llm_config(self, config_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM llm_configs WHERE id = ?", (config_id,))

    # --- FTS5 Full-Text Search ---

    def fts_upsert(
        self,
        note_id: str,
        title: str,
        body: str,
        tags: str,
        note_type: str,
        confidence: str,
        source_ids: str,
    ) -> None:
        with self.connection() as conn:
            # Delete old entry if exists, then insert
            conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
            conn.execute(
                "INSERT INTO notes_fts (note_id, title, body, tags, note_type, confidence, source_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (note_id, title, body, tags, note_type, confidence, source_ids),
            )

    def fts_search(
        self,
        query: str,
        fields: list[str] | None = None,
        filter_type: str | None = None,
        filter_confidence: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Search using FTS5 with BM25 ranking.

        query: FTS5 query string (supports AND, OR, NOT, phrases, prefix*, NEAR())
        fields: restrict search to specific columns (title, body, tags)
                Only applied if query doesn't already contain ':' field specifiers.
        """
        # If query already has field specifiers (e.g. "title : kafka"), pass through as-is
        has_field_spec = " : " in query or ":" in query.split()[0] if query.strip() else False

        if fields and not has_field_spec:
            # Wrap the query with field restrictions using OR
            valid_fields = [f for f in fields if f in ("title", "body", "tags")]
            if valid_fields:
                fts_query = " OR ".join(f"{f} : ({query})" for f in valid_fields)
            else:
                fts_query = query
        else:
            fts_query = query

        # Escape for safety but allow FTS5 operators
        with self.connection() as conn:
            sql = """
                SELECT note_id, title, snippet(notes_fts, 2, '<mark>', '</mark>', '...', 40) as snippet,
                       tags, note_type, confidence, source_ids,
                       bm25(notes_fts, 0, 10.0, 1.0, 5.0, 0, 0, 0) as rank
                FROM notes_fts
                WHERE notes_fts MATCH ?
            """
            params: list = [fts_query]

            if filter_type:
                sql += " AND note_type = ?"
                params.append(filter_type)
            if filter_confidence:
                sql += " AND confidence = ?"
                params.append(filter_confidence)

            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception:
                # If query syntax is invalid, try wrapping each term in quotes
                safe_query = " AND ".join(f'"{t}"' for t in query.split() if t)
                if fields:
                    safe_query = " OR ".join(f'{f} : ({safe_query})' for f in fields if f in ("title", "body", "tags"))
                params[0] = safe_query
                try:
                    rows = conn.execute(sql, params).fetchall()
                except Exception:
                    return []

            return [dict(r) for r in rows]

    def fts_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM notes_fts").fetchone()
            return row["c"] if row else 0

    def fts_rebuild(self) -> None:
        """Clear and rebuild FTS index — called explicitly, not on every startup."""
        with self.connection() as conn:
            conn.execute("DELETE FROM notes_fts")

    # --- Note Annotations ---

    def get_annotation(self, note_id: str) -> NoteAnnotation | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM note_annotations WHERE note_id = ?", (note_id,)).fetchone()
            if row is None:
                return None
            return NoteAnnotation(**dict(row))

    def save_annotation(self, ann: NoteAnnotation) -> None:
        ann.updated_at = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO note_annotations
                   (note_id, starred, labels, user_tags, summary, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ann.note_id, ann.starred, ann.labels, ann.user_tags,
                 ann.summary, ann.updated_at),
            )

    def toggle_star(self, note_id: str) -> bool:
        ann = self.get_annotation(note_id)
        if ann is None:
            ann = NoteAnnotation(note_id=note_id, starred=1)
        else:
            ann.starred = 0 if ann.starred else 1
        self.save_annotation(ann)
        return bool(ann.starred)

    def set_labels(self, note_id: str, labels: list[str]) -> None:
        ann = self.get_annotation(note_id) or NoteAnnotation(note_id=note_id)
        ann.labels = ",".join(l.strip() for l in labels if l.strip())
        self.save_annotation(ann)

    def set_user_tags(self, note_id: str, user_tags: list[str]) -> None:
        ann = self.get_annotation(note_id) or NoteAnnotation(note_id=note_id)
        ann.user_tags = ",".join(t.strip() for t in user_tags if t.strip())
        self.save_annotation(ann)

    def set_summary(self, note_id: str, summary: str) -> None:
        ann = self.get_annotation(note_id) or NoteAnnotation(note_id=note_id)
        ann.summary = summary
        self.save_annotation(ann)

    def get_starred_notes(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT note_id FROM note_annotations WHERE starred = 1"
            ).fetchall()
            return [r["note_id"] for r in rows]

    def search_by_label(self, label: str) -> list[str]:
        with self.connection() as conn:
            pattern = f"%{label}%"
            rows = conn.execute(
                "SELECT note_id FROM note_annotations WHERE labels LIKE ?", (pattern,)
            ).fetchall()
            return [r["note_id"] for r in rows]

    def search_by_user_tag(self, tag: str) -> list[str]:
        with self.connection() as conn:
            pattern = f"%{tag}%"
            rows = conn.execute(
                "SELECT note_id FROM note_annotations WHERE user_tags LIKE ?", (pattern,)
            ).fetchall()
            return [r["note_id"] for r in rows]

    def get_all_labels(self) -> list[tuple[str, int]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT labels FROM note_annotations WHERE labels != ''"
            ).fetchall()
        from collections import Counter
        counter: Counter[str] = Counter()
        for r in rows:
            for label in r["labels"].split(","):
                label = label.strip()
                if label:
                    counter[label] += 1
        return counter.most_common(50)

    def get_all_user_tags(self) -> list[tuple[str, int]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT user_tags FROM note_annotations WHERE user_tags != ''"
            ).fetchall()
        from collections import Counter
        counter: Counter[str] = Counter()
        for r in rows:
            for tag in r["user_tags"].split(","):
                tag = tag.strip()
                if tag:
                    counter[tag] += 1
        return counter.most_common(50)
