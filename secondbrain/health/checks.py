"""Vault health checks: orphans, broken links, duplicates, stale notes, missing provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from secondbrain.database import Database, Note
from secondbrain.vault.frontmatter import parse_frontmatter
from secondbrain.vault.manager import VaultManager


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


@dataclass
class HealthReport:
    orphan_notes: list[str] = field(default_factory=list)
    broken_links: list[dict[str, str]] = field(default_factory=list)
    duplicate_candidates: list[list[str]] = field(default_factory=list)
    stale_notes: list[str] = field(default_factory=list)
    missing_provenance: list[str] = field(default_factory=list)
    weak_summaries: list[str] = field(default_factory=list)
    uncompiled_sources: int = 0

    @property
    def total_issues(self) -> int:
        return (
            len(self.orphan_notes)
            + len(self.broken_links)
            + len(self.duplicate_candidates)
            + len(self.stale_notes)
            + len(self.missing_provenance)
            + len(self.weak_summaries)
            + self.uncompiled_sources
        )

    def to_markdown(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"# Vault Health Report — {today}\n"]

        if self.total_issues == 0:
            lines.append("All clear! No issues found.\n")
            return "\n".join(lines)

        lines.append("## Needs Attention\n")

        if self.orphan_notes:
            lines.append(f"- {len(self.orphan_notes)} orphan notes (no inbound or outbound links)")
        if self.broken_links:
            lines.append(f"- {len(self.broken_links)} broken links")
        if self.duplicate_candidates:
            lines.append(f"- {len(self.duplicate_candidates)} duplicate candidates")
        if self.stale_notes:
            lines.append(f"- {len(self.stale_notes)} stale notes")
        if self.missing_provenance:
            lines.append(f"- {len(self.missing_provenance)} notes missing provenance")
        if self.weak_summaries:
            lines.append(f"- {len(self.weak_summaries)} notes with no summary section")
        if self.uncompiled_sources:
            lines.append(f"- {self.uncompiled_sources} sources imported but not compiled")

        lines.append("")

        if self.orphan_notes:
            lines.append("## Orphan Notes\n")
            for n in self.orphan_notes:
                lines.append(f"- [[{n}]]")
            lines.append("")

        if self.broken_links:
            lines.append("## Broken Links\n")
            for bl in self.broken_links:
                lines.append(f"- [[{bl['target']}]] in {bl['source']}")
            lines.append("")

        if self.duplicate_candidates:
            lines.append("## Duplicate Candidates\n")
            for group in self.duplicate_candidates:
                lines.append(f"- {' / '.join(f'[[{n}]]' for n in group)}")
            lines.append("")

        if self.stale_notes:
            lines.append("## Stale Notes\n")
            for n in self.stale_notes:
                lines.append(f"- [[{n}]]")
            lines.append("")

        if self.missing_provenance:
            lines.append("## Missing Provenance\n")
            for n in self.missing_provenance:
                lines.append(f"- [[{n}]]")
            lines.append("")

        return "\n".join(lines)


def run_health_check(vault: VaultManager, db: Database, stale_days: int = 180) -> HealthReport:
    report = HealthReport()
    all_note_files = vault.list_all_notes()
    note_titles: dict[str, Path] = {}
    note_links: dict[str, set[str]] = {}
    inbound_links: dict[str, int] = {}

    for note_path in all_note_files:
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(content)
        title = fm.title if fm else note_path.stem
        note_titles[title] = note_path

        # Extract wikilinks
        links = set(WIKILINK_RE.findall(content))
        note_links[title] = links
        for link_target in links:
            inbound_links[link_target] = inbound_links.get(link_target, 0) + 1

    # Check orphan notes
    for title in note_titles:
        outbound = note_links.get(title, set())
        inbound = inbound_links.get(title, 0)
        if not outbound and inbound == 0:
            report.orphan_notes.append(title)

    # Check broken links
    for title, links in note_links.items():
        for target in links:
            if target not in note_titles:
                report.broken_links.append({"source": title, "target": target})

    # Check duplicate candidates (similar titles)
    titles_list = list(note_titles.keys())
    seen_dupes: set[frozenset[str]] = set()
    for i, t1 in enumerate(titles_list):
        for t2 in titles_list[i + 1:]:
            if _similar_titles(t1, t2):
                key = frozenset([t1, t2])
                if key not in seen_dupes:
                    seen_dupes.add(key)
                    report.duplicate_candidates.append([t1, t2])

    # Check stale notes
    now = datetime.now(timezone.utc)
    for note_path in all_note_files:
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, _ = parse_frontmatter(content)
        if fm and fm.updated:
            try:
                updated = datetime.strptime(fm.updated, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if (now - updated).days > stale_days:
                    report.stale_notes.append(fm.title)
            except ValueError:
                pass

    # Check missing provenance
    for note_path in all_note_files:
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, _ = parse_frontmatter(content)
        if fm and not fm.source_ids:
            report.missing_provenance.append(fm.title)

    # Check weak summaries
    for note_path in all_note_files:
        content = note_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(content)
        if fm and "## Summary" not in body and fm.note_type != "daily":
            report.weak_summaries.append(fm.title)

    # Uncompiled sources
    report.uncompiled_sources = len(db.get_uncompiled_sources())

    return report


def _similar_titles(t1: str, t2: str) -> bool:
    n1 = t1.lower().replace("-", " ").replace("_", " ").strip()
    n2 = t2.lower().replace("-", " ").replace("_", " ").strip()

    if n1 == n2:
        return True

    # One is substring of other
    if n1 in n2 or n2 in n1:
        return len(min(n1, n2, key=len)) > 3

    # Word overlap
    words1 = set(n1.split())
    words2 = set(n2.split())
    if not words1 or not words2:
        return False
    overlap = words1 & words2
    smaller = min(len(words1), len(words2))
    return smaller > 0 and len(overlap) / smaller >= 0.8
