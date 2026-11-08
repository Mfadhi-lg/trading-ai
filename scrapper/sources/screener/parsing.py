"""Source-specific Screener parsing parsing helpers."""

from __future__ import annotations

from bs4 import Tag
from ...core.nums import clean_text


def _node_text(node: Tag | None) -> tuple[str, str]:
    if node is None:
        return "", ""
    return node.get_text("", strip=False), clean_text(node.get_text(" ", strip=True))


def _first_link(node: Tag) -> str | None:
    link = node.find("a", href=True)
    return str(link["href"]) if isinstance(link, Tag) else None

# Preserve the legacy facade identity for existing serialized callables.
_node_text.__module__ = "scrapper.sources.screener.company"
_first_link.__module__ = "scrapper.sources.screener.company"
