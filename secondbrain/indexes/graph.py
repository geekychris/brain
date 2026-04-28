"""Graph analysis: co-mention clustering, concept relationships from text content."""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from secondbrain.database import Database, Note
from secondbrain.vault.manager import VaultManager
from secondbrain.vault.frontmatter import parse_frontmatter

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Module-level cache
_index_cache: dict[str, tuple[float, dict]] = {}  # vault_path -> (timestamp, concept_index)
_cache_lock = threading.Lock()
CACHE_TTL = 120  # seconds


@dataclass
class ConceptInfo:
    title: str
    note_id: str | None
    note_type: str | None
    mention_count: int
    source_notes: list[str] = field(default_factory=list)
    co_mentions: dict[str, int] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class GraphData:
    nodes: list[dict]
    edges: list[dict]


def _get_cached_index(vault: VaultManager, db: Database) -> dict[str, ConceptInfo]:
    key = str(vault.root)
    with _cache_lock:
        if key in _index_cache:
            ts, idx = _index_cache[key]
            if time.time() - ts < CACHE_TTL:
                return idx

    idx = _build_concept_index(vault, db)
    with _cache_lock:
        _index_cache[key] = (time.time(), idx)
    return idx


def invalidate_cache(vault: VaultManager) -> None:
    with _cache_lock:
        _index_cache.pop(str(vault.root), None)


def _build_concept_index(vault: VaultManager, db: Database) -> dict[str, ConceptInfo]:
    """Build concept index using both wikilinks AND plain-text title matching."""
    all_notes = db.list_notes()
    note_by_title: dict[str, Note] = {}
    for n in all_notes:
        note_by_title[n.title.lower()] = n

    # Collect concept/entity titles for text matching.
    # Only use entity names (curated) and concept-type note titles, not source note titles
    # (source titles are often long sentences that match everywhere).
    all_titles: set[str] = set()
    for n in all_notes:
        if n.note_type == "concept":
            all_titles.add(n.title)
    entities = db.list_entities()
    for e in entities:
        all_titles.add(e.name)
        if e.aliases_json:
            try:
                for alias in json.loads(e.aliases_json):
                    if len(alias) > 3:
                        all_titles.add(alias)
            except (json.JSONDecodeError, ValueError):
                pass

    # Filter out short/generic words
    STOP_WORDS = {
        "type", "title", "source", "date", "name", "tags", "created", "updated",
        "confidence", "aliases", "summary", "note", "notes", "page", "link", "links",
        "text", "data", "file", "path", "code", "test", "list", "info", "true",
        "false", "none", "null", "todo", "item", "class", "field", "table", "view",
        "key", "value", "idea", "ideas", "line", "port", "comp", "ring", "mark",
        "markdown", "source id", "concept", "document", "related", "open", "based",
        "concept extracted from", "key ideas", "open questions", "related notes",
        "source_ids", "high", "medium", "extracted", "untitled",
        "light", "service", "platform", "person", "event", "connect", "project",
        "topic", "model", "system", "group", "process", "image", "video", "email",
        "integration", "highlights", "report", "review", "update", "example",
        "feature", "design", "article", "content", "format", "method", "action",
        "error", "state", "status", "version", "module", "index", "input", "output",
        "level", "order", "point", "block", "store", "layer", "stage", "entry",
        "label", "token", "issue", "query", "scope", "agent", "cache", "proxy",
        "queue", "build", "route", "guard", "panel", "patch", "frame",
    }
    # For text matching: require multi-word phrases (>=2 words, >=8 chars)
    # Single words match too broadly against note bodies
    def _is_good_title(t: str) -> bool:
        tl = t.lower()
        if tl in STOP_WORDS:
            return False
        words = t.split()
        if len(words) >= 2:
            return len(t) >= 8
        return False  # skip single-word text matching; wikilinks still work

    all_titles = {t for t in all_titles if _is_good_title(t)}

    # Pre-compute lowercase for matching
    title_lookup: dict[str, str] = {}  # lowercase -> canonical title
    for t in all_titles:
        tl = t.lower()
        if tl not in title_lookup or len(t) > len(title_lookup[tl]):
            title_lookup[tl] = t

    concepts: dict[str, ConceptInfo] = {}
    note_mentions: dict[str, set[str]] = {}  # note_id -> set of concept keys mentioned

    for note in all_notes:
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(content)
        body_lower = body.lower()

        # Register the note itself as a concept
        note_key = note.title.lower()
        if note_key not in concepts:
            concepts[note_key] = ConceptInfo(
                title=note.title, note_id=note.id, note_type=note.note_type,
                mention_count=0, tags=fm.tags if fm else [],
            )
        else:
            ci = concepts[note_key]
            if ci.note_id is None:
                ci.note_id = note.id
                ci.note_type = note.note_type
                ci.tags = fm.tags if fm else []

        # Find all concepts/entities mentioned in this note's body
        mentioned: set[str] = set()

        # Wikilinks (from full content)
        for link_title in WIKILINK_RE.findall(content):
            lt = link_title.lower()
            if lt not in STOP_WORDS and len(lt) >= 4:
                mentioned.add(lt)

        # Plain text matching in body only, require word boundary
        for tl, canonical in title_lookup.items():
            if tl == note_key:
                continue  # don't self-match
            if tl in body_lower:
                mentioned.add(tl)

        note_mentions[note.id] = mentioned

        for m in mentioned:
            if m not in concepts:
                target_note = note_by_title.get(m)
                concepts[m] = ConceptInfo(
                    title=title_lookup.get(m, m),
                    note_id=target_note.id if target_note else None,
                    note_type=target_note.note_type if target_note else None,
                    mention_count=0,
                )
            concepts[m].mention_count += 1
            concepts[m].source_notes.append(note.id)

    # Build co-mention edges
    for note_id, mentioned in note_mentions.items():
        m_list = sorted(mentioned)
        for i, a in enumerate(m_list):
            for b in m_list[i + 1:]:
                if a in concepts and b in concepts:
                    concepts[a].co_mentions[b] = concepts[a].co_mentions.get(b, 0) + 1
                    concepts[b].co_mentions[a] = concepts[b].co_mentions.get(a, 0) + 1

    return concepts


def get_concept_detail(
    concept_title: str,
    vault: VaultManager,
    db: Database,
    max_related: int = 30,
) -> dict | None:
    concepts = _get_cached_index(vault, db)
    key = concept_title.lower()
    if key not in concepts:
        return None

    ci = concepts[key]

    source_notes = []
    seen = set()
    for nid in ci.source_notes:
        if nid not in seen:
            seen.add(nid)
            note = db.get_note(nid)
            if note:
                source_notes.append(note)

    related = sorted(ci.co_mentions.items(), key=lambda x: -x[1])[:max_related]
    related_concepts = []
    for title_lower, count in related:
        rc = concepts.get(title_lower)
        if rc:
            related_concepts.append({
                "title": rc.title,
                "note_id": rc.note_id,
                "co_mention_count": count,
                "mention_count": rc.mention_count,
            })

    return {
        "title": ci.title,
        "note_id": ci.note_id,
        "note_type": ci.note_type,
        "mention_count": ci.mention_count,
        "tags": ci.tags,
        "source_notes": source_notes,
        "related_concepts": related_concepts,
    }


def build_graph_data(
    vault: VaultManager,
    db: Database,
    min_mentions: int = 2,
    min_co_mentions: int = 1,
    max_nodes: int = 200,
) -> GraphData:
    concepts = _get_cached_index(vault, db)
    total_notes = len(db.list_notes())

    # Exclude overly generic concepts (appear in >3% of notes)
    max_frequency = max(total_notes * 0.03, 30) if total_notes > 0 else 30

    filtered = {
        k: v for k, v in concepts.items()
        if v.mention_count >= min_mentions and v.mention_count <= max_frequency
    }

    # Rank by a balance of mentions and connections
    for v in filtered.values():
        v._graph_score = v.mention_count + len(v.co_mentions) * 0.5  # type: ignore[attr-defined]
    sorted_concepts = sorted(filtered.values(), key=lambda c: -c._graph_score)[:max_nodes]  # type: ignore[attr-defined]
    included = {c.title.lower() for c in sorted_concepts}

    nodes = []
    for ci in sorted_concepts:
        co_count = sum(1 for t, w in ci.co_mentions.items() if t in included and w >= min_co_mentions)
        nodes.append({
            "id": ci.title.lower(),
            "label": ci.title,
            "note_id": ci.note_id,
            "type": ci.note_type or "reference",
            "mentions": ci.mention_count,
            "connections": co_count,
            "tags": ci.tags[:3],
        })

    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for ci in sorted_concepts:
        for target_lower, count in ci.co_mentions.items():
            if count < min_co_mentions:
                continue
            if target_lower not in included:
                continue
            edge_key = tuple(sorted([ci.title.lower(), target_lower]))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append({
                "source": ci.title.lower(),
                "target": target_lower,
                "weight": count,
            })

    return GraphData(nodes=nodes, edges=edges)


def build_focused_graph(
    focus_term: str,
    vault: VaultManager,
    db: Database,
    min_mentions: int = 2,
    min_co_mentions: int = 1,
    max_nodes: int = 200,
    depth: int = 2,
) -> GraphData:
    """Build a graph centered on a concept/entity — shows its cluster to N hops."""
    concepts = _get_cached_index(vault, db)
    total_notes = len(db.list_notes())
    max_frequency = max(total_notes * 0.03, 30) if total_notes > 0 else 30
    focus_key = focus_term.lower()

    # BFS from focus node through co-mentions
    visited: set[str] = set()
    frontier: set[str] = {focus_key}

    for _ in range(depth):
        next_frontier: set[str] = set()
        for key in frontier:
            if key in visited:
                continue
            visited.add(key)
            ci = concepts.get(key)
            if ci is None:
                continue
            for neighbor, weight in ci.co_mentions.items():
                if weight >= min_co_mentions and neighbor not in visited:
                    nc = concepts.get(neighbor)
                    if nc and nc.mention_count >= min_mentions and nc.mention_count <= max_frequency:
                        next_frontier.add(neighbor)
        frontier = next_frontier

    visited.update(frontier)

    # Cap to max_nodes, prioritize by relevance to focus
    focus_ci = concepts.get(focus_key)
    if not focus_ci:
        return GraphData(nodes=[], edges=[])

    def _relevance(key: str) -> float:
        if key == focus_key:
            return 1e9
        return focus_ci.co_mentions.get(key, 0) * 10 + concepts.get(key, ConceptInfo("", None, None, 0)).mention_count

    sorted_keys = sorted(visited, key=lambda k: -_relevance(k))[:max_nodes]
    included = set(sorted_keys)

    nodes = []
    for key in sorted_keys:
        ci = concepts.get(key)
        if not ci:
            continue
        co_count = sum(1 for t, w in ci.co_mentions.items() if t in included and w >= min_co_mentions)
        nodes.append({
            "id": ci.title.lower(),
            "label": ci.title,
            "note_id": ci.note_id,
            "type": ci.note_type or "reference",
            "mentions": ci.mention_count,
            "connections": co_count,
            "tags": ci.tags[:3],
            "is_focus": key == focus_key,
        })

    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for key in sorted_keys:
        ci = concepts.get(key)
        if not ci:
            continue
        for target_lower, count in ci.co_mentions.items():
            if count < min_co_mentions or target_lower not in included:
                continue
            edge_key = tuple(sorted([key, target_lower]))
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append({"source": key, "target": target_lower, "weight": count})

    return GraphData(nodes=nodes, edges=edges)


def find_clusters(graph: GraphData) -> list[list[dict]]:
    """Find connected components in a graph. Returns list of node groups."""
    if not graph.nodes:
        return []

    adj: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    node_map = {n["id"]: n for n in graph.nodes}
    visited: set[str] = set()
    clusters: list[list[dict]] = []

    for node in graph.nodes:
        nid = node["id"]
        if nid in visited:
            continue
        # BFS
        cluster_ids: list[str] = []
        queue = [nid]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster_ids.append(current)
            for neighbor in adj.get(current, set()):
                if neighbor not in visited and neighbor in node_map:
                    queue.append(neighbor)
        if cluster_ids:
            clusters.append([node_map[cid] for cid in cluster_ids if cid in node_map])

    clusters.sort(key=lambda c: -len(c))
    return clusters


def summarize_cluster(
    cluster_nodes: list[dict],
    vault: VaultManager,
    db: Database,
    llm,
) -> str:
    """Use LLM to summarize a cluster of co-mentioned concepts."""
    concept_names = [n["label"] for n in cluster_nodes]

    # Gather excerpts from source notes for these concepts
    concepts = _get_cached_index(vault, db)
    all_note_ids: set[str] = set()
    for n in cluster_nodes:
        ci = concepts.get(n["id"])
        if ci:
            for nid in ci.source_notes[:5]:  # cap per concept
                all_note_ids.add(nid)

    excerpts = []
    for nid in list(all_note_ids)[:20]:  # cap total
        note = db.get_note(nid)
        if not note:
            continue
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace")
        # Take first 300 chars of body
        from secondbrain.vault.frontmatter import parse_frontmatter
        _, body = parse_frontmatter(content)
        excerpts.append(f"### {note.title}\n{body[:300]}")

    context = "\n\n".join(excerpts)

    prompt = f"""You are summarizing a cluster of related concepts from a personal knowledge base.

Concepts in this cluster: {', '.join(concept_names)}

Here are excerpts from notes related to these concepts:
---
{context[:6000]}
---

Write a concise summary (3-5 paragraphs) that:
1. Explains what ties these concepts together
2. Highlights the key themes and relationships
3. Notes any interesting patterns or tensions
4. Suggests what areas might need more exploration

Write in clear prose, not bullet points. Reference specific concepts by name."""

    system = "You are a knowledge base analyst. Summarize clearly and faithfully based only on the provided context."

    resp = llm.generate(prompt, system=system, temperature=0.4)
    return resp.text


def get_entity_detail(
    entity_id: str,
    vault: VaultManager,
    db: Database,
) -> dict | None:
    entity = db.get_entity(entity_id)
    if not entity:
        return None

    aliases = []
    if entity.aliases_json:
        try:
            aliases = json.loads(entity.aliases_json)
        except (json.JSONDecodeError, ValueError):
            pass

    search_terms = [entity.name.lower()] + [a.lower() for a in aliases]

    all_notes = db.list_notes()
    mentioning_notes = []
    mention_note_ids: set[str] = set()

    for note in all_notes:
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace").lower()
        if any(term in content for term in search_terms if len(term) > 2):
            mentioning_notes.append(note)
            mention_note_ids.add(note.id)

    # Find co-occurring entities
    co_entities: Counter[str] = Counter()
    all_entities = db.list_entities()
    entity_names = {e.id: e for e in all_entities}

    for note in mentioning_notes[:100]:  # cap for performance
        note_path = vault.root / note.path
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8", errors="replace").lower()
        for other in all_entities:
            if other.id == entity.id:
                continue
            if len(other.name) > 2 and other.name.lower() in content:
                co_entities[other.id] += 1

    related_entities = []
    for eid, count in co_entities.most_common(20):
        e = entity_names.get(eid)
        if e:
            related_entities.append({
                "id": e.id,
                "name": e.name,
                "type": e.entity_type,
                "co_mention_count": count,
            })

    return {
        "entity": entity,
        "aliases": aliases,
        "mentioning_notes": mentioning_notes,
        "related_entities": related_entities,
    }
