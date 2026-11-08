"""Bounded, source-faithful extraction for stored credit-rating HTML pages."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from .extraction import (
    DocumentElement,
    DocumentPage,
    ExtractedDocument,
    normalize_extracted_text,
    text_usability,
)


HTML = "text/html"
_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table")


def _table_rows(table: Tag) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for row in table.find_all("tr"):
        cells = tuple(
            normalize_extracted_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(("th", "td"), recursive=False)
        )
        if any(cells):
            rows.append(cells)
    return tuple(rows)


def extract_rating_html(content: bytes) -> ExtractedDocument:
    """Extract visible publisher HTML into one auditable logical page.

    The immutable source is a web page rather than a paginated file, so D03
    represents it as a single logical page and retains ordered block/table
    elements. Script, style, template, and challenge-only surfaces contribute
    no extracted text.
    """

    soup = BeautifulSoup(content, "html.parser")
    for hidden in soup.find_all(("script", "style", "noscript", "template", "svg")):
        hidden.decompose()
    root = soup.body or soup
    elements: list[DocumentElement] = []
    text_parts: list[str] = []
    for tag in root.find_all(_BLOCK_TAGS):
        if tag.name != "table" and tag.find_parent("table") is not None:
            continue
        if tag.name == "table":
            rows = _table_rows(tag)
            if not rows:
                continue
            elements.append(
                DocumentElement(
                    position=len(elements),
                    kind="table",
                    text_origin="native",
                    rows=rows,
                )
            )
            text_parts.extend(" | ".join(row) for row in rows)
            continue
        text = normalize_extracted_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        elements.append(
            DocumentElement(
                position=len(elements),
                kind="text_block",
                text_origin="native",
                text_raw=text,
                title_raw=text if tag.name.startswith("h") else None,
                reference={"html_tag": tag.name},
            )
        )
        text_parts.append(text)
    native_text = normalize_extracted_text("\n".join(text_parts))
    if not native_text:
        native_text = normalize_extracted_text(root.get_text("\n", strip=True))
        if native_text:
            elements.append(
                DocumentElement(
                    position=0,
                    kind="text_block",
                    text_origin="native",
                    text_raw=native_text,
                    reference={"html_tag": root.name or "document"},
                )
            )
    usable, reason = text_usability(native_text)
    title = normalize_extracted_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    page = DocumentPage(
        number=1,
        kind="page",
        title_raw=title or None,
        native_text=native_text,
        ocr_text="",
        selected_text=native_text if usable else "",
        selected_text_origin="native" if usable else "none",
        native_text_usable=usable,
        native_unusable_reason=reason,
        extraction_confidence=None,
        layout={
            "source_format": "html",
            "logical_page": True,
            "parser": "html.parser",
        },
        elements=tuple(elements),
    )
    return ExtractedDocument(
        media_type=HTML,
        pages=(page,),
        metadata={"format": "html", "title": title or None},
    )
