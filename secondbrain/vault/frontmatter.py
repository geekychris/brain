"""Frontmatter parsing and generation for Obsidian-compatible Markdown notes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class NoteFrontmatter:
    title: str
    note_type: str = "source"
    aliases: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: str = "medium"
    created: str = ""
    updated: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[NoteFrontmatter | None, str]:
    m = FRONTMATTER_RE.match(content)
    if not m:
        return None, content

    raw = yaml.safe_load(m.group(1))
    if not isinstance(raw, dict):
        return None, content

    body = content[m.end():]
    fm = NoteFrontmatter(
        title=raw.get("title", ""),
        note_type=raw.get("type", "source"),
        aliases=raw.get("aliases", []),
        source_ids=raw.get("source_ids", []),
        tags=raw.get("tags", []),
        confidence=raw.get("confidence", "medium"),
        created=raw.get("created", ""),
        updated=raw.get("updated", ""),
        extra={k: v for k, v in raw.items()
               if k not in ("title", "type", "aliases", "source_ids", "tags",
                            "confidence", "created", "updated")},
    )
    return fm, body


def render_frontmatter(fm: NoteFrontmatter) -> str:
    d: dict[str, Any] = {"title": fm.title, "type": fm.note_type}
    if fm.aliases:
        d["aliases"] = fm.aliases
    if fm.source_ids:
        d["source_ids"] = fm.source_ids
    if fm.tags:
        d["tags"] = fm.tags
    d["confidence"] = fm.confidence
    if fm.created:
        d["created"] = fm.created
    if fm.updated:
        d["updated"] = fm.updated
    d.update(fm.extra)
    return "---\n" + yaml.dump(d, default_flow_style=False, sort_keys=False) + "---\n"


def build_note(fm: NoteFrontmatter, body: str) -> str:
    return render_frontmatter(fm) + "\n" + body
