"""Query engine: search vault notes and answer questions with citations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from secondbrain.database import Database, Note
from secondbrain.llm.client import LLMClient
from secondbrain.vault.frontmatter import parse_frontmatter
from secondbrain.vault.manager import VaultManager


QA_SYSTEM = """You are answering questions from a personal knowledge vault.
Answer ONLY from the provided context notes. Distinguish between:
- Directly sourced: cite the exact note
- Synthesized: cite supporting notes
- Speculative: mark as inference
- Unknown: say what is missing
- Contradictory: show conflicting sources

Return JSON with: answer, confidence (high/medium/low), sources (list of note titles),
answer_type (sourced/synthesized/speculative/unknown/contradictory)."""

QA_PROMPT = """Answer this question using ONLY the vault notes provided below.

Question: {question}

Context notes:
---
{context}
---

Return JSON only."""


@dataclass
class SearchResult:
    note: Note
    excerpt: str
    relevance: float


@dataclass
class Answer:
    text: str
    confidence: str
    sources: list[str]
    answer_type: str


def search_vault(
    query: str,
    vault: VaultManager,
    db: Database,
    max_results: int = 10,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    query_lower = query.lower()
    query_terms = query_lower.split()

    # Title-based search from DB
    title_matches = db.search_notes_fulltext(query)

    # Content-based search over files
    all_notes = db.list_notes()
    scored: list[tuple[Note, float, str]] = []

    for note in all_notes:
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace")
        content_lower = content.lower()

        score = 0.0
        # Title match
        if query_lower in note.title.lower():
            score += 2.0

        # Content term matching
        for term in query_terms:
            if term in content_lower:
                score += 1.0
            if term in note.title.lower():
                score += 0.5

        if score > 0:
            # Extract best excerpt
            excerpt = _extract_excerpt(content, query_terms)
            scored.append((note, score, excerpt))

    scored.sort(key=lambda x: x[1], reverse=True)

    for note, score, excerpt in scored[:max_results]:
        # Normalize score to 0-1
        max_score = 2.0 + len(query_terms) * 1.5
        relevance = min(score / max_score, 1.0)
        results.append(SearchResult(note=note, excerpt=excerpt, relevance=relevance))

    return results


def _extract_excerpt(content: str, terms: list[str], context_chars: int = 200) -> str:
    content_lower = content.lower()
    best_pos = -1
    best_term_count = 0

    for i, term in enumerate(terms):
        pos = content_lower.find(term)
        if pos >= 0:
            # Count how many terms are near this position
            nearby = sum(
                1 for t in terms
                if content_lower.find(t, max(0, pos - context_chars), pos + context_chars) >= 0
            )
            if nearby > best_term_count:
                best_term_count = nearby
                best_pos = pos

    if best_pos < 0:
        return content[:context_chars].strip()

    start = max(0, best_pos - context_chars // 2)
    end = min(len(content), best_pos + context_chars // 2)
    excerpt = content[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt = excerpt + "..."
    return excerpt


def ask_vault(
    question: str,
    vault: VaultManager,
    db: Database,
    llm: LLMClient,
    max_context_notes: int = 5,
) -> Answer:
    results = search_vault(question, vault, db, max_results=max_context_notes)

    if not results:
        return Answer(
            text="No relevant notes found in the vault for this question.",
            confidence="low",
            sources=[],
            answer_type="unknown",
        )

    # Build context
    context_parts = []
    for r in results:
        note_path = vault.root / r.note.path
        if note_path.exists():
            content = note_path.read_text(encoding="utf-8", errors="replace")
            # Truncate long notes
            if len(content) > 2000:
                content = content[:2000] + "\n[... truncated ...]"
            context_parts.append(f"### {r.note.title}\n{content}")

    context = "\n\n---\n\n".join(context_parts)

    prompt = QA_PROMPT.format(question=question, context=context)
    resp = llm.generate(prompt, system=QA_SYSTEM)

    try:
        data = resp.as_json()
        return Answer(
            text=data.get("answer", resp.text),
            confidence=data.get("confidence", "medium"),
            sources=data.get("sources", [r.note.title for r in results]),
            answer_type=data.get("answer_type", "synthesized"),
        )
    except (json.JSONDecodeError, ValueError):
        return Answer(
            text=resp.text,
            confidence="medium",
            sources=[r.note.title for r in results],
            answer_type="synthesized",
        )
