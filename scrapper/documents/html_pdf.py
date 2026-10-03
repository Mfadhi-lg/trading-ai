"""Deterministic, offline PDF rendering for validated publisher HTML reports.

The publisher response remains the immutable source.  This renderer creates a
portable text-first derivative without making subresource requests, executing
scripts, or pretending that a rendered derivative is publisher-original.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


HTML_PDF_RENDERER = "validated-html-text-pdf"
HTML_PDF_RENDERER_VERSION = "1.1.0"
HTML_PDF_MEDIA_TYPE = "application/pdf"

_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "td")
_REMOVED_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "nav",
    "footer",
    "form",
    "button",
)


@dataclass(frozen=True)
class HtmlPdfRender:
    body: bytes
    title: str
    visible_character_count: int
    block_count: int


def _clean_text(value: str) -> str:
    without_controls = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return " ".join(without_controls.split())


def _visible_blocks(content: bytes) -> tuple[str, list[tuple[str, str]]]:
    soup = BeautifulSoup(content, "html.parser")
    for node in soup.find_all(_REMOVED_TAGS):
        node.decompose()
    title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    root = soup.find("main") or soup.find("article") or soup.body or soup
    # Word-generated rating HTML uses large nested tables for page layout.
    # Flattening every enclosing table repeats entire sections. Only leaf
    # tables with multiple columns are data tables; visit text blocks inside
    # layout tables normally and consume each data table exactly once.
    data_tables = {
        id(table) for table in root.find_all("table")
        if not table.find("table")
        and any(len(row.find_all(("td", "th"), recursive=False)) >= 2
                for row in table.find_all("tr"))
    }
    blocks: list[tuple[str, str]] = []
    previous: tuple[str, str] | None = None
    for tag in root.find_all(_BLOCK_TAGS):
        if not isinstance(tag, Tag):
            continue
        if any(id(parent) in data_tables for parent in tag.find_parents("table")):
            continue
        if tag.name == "table":
            if id(tag) not in data_tables:
                continue
            rows = []
            for row in tag.find_all("tr"):
                cells = [
                    _clean_text(cell.get_text(" ", strip=True))
                    for cell in row.find_all(("th", "td"), recursive=False)
                ]
                if any(cells):
                    rows.append(" | ".join(cells))
            text = "\n".join(rows)
            kind = "table"
        else:
            if tag.find(_BLOCK_TAGS) is not None:
                continue
            text = _clean_text(tag.get_text(" ", strip=True))
            bold = tag.find(("b", "strong"))
            heading = tag.name.startswith("h") or (
                bool(bold) and len(text) < 160 and _clean_text(bold.get_text(" ", strip=True)) == text
            )
            kind = "heading" if heading else "list" if tag.name == "li" else "body"
        if not text:
            continue
        block = (kind, text)
        if block != previous:
            blocks.append(block)
            previous = block
    if not blocks:
        fallback = _clean_text(root.get_text(" ", strip=True))
        if fallback:
            blocks.append(("body", fallback))
    return title, blocks


def _paragraphs(text: str, *, chunk_size: int = 3500) -> list[str]:
    """Bound individual ReportLab paragraphs without dropping source text."""

    if len(text) <= chunk_size:
        return [text]
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        additional = len(word) + (1 if current else 0)
        if current and length + additional > chunk_size:
            chunks.append(" ".join(current))
            current = []
            length = 0
        current.append(word)
        length += len(word) + (1 if length else 0)
    if current:
        chunks.append(" ".join(current))
    return chunks


def render_validated_html_to_pdf(
    content: bytes,
    *,
    source_url: str,
    document_title: str | None = None,
) -> HtmlPdfRender:
    """Render already-validated HTML into a deterministic, text-first PDF.

    No linked stylesheet, image, font, or script is fetched.  Callers must run
    their artifact-specific genuine-content classifier before invoking this.
    """

    if not content:
        raise ValueError("Cannot render empty HTML")
    parsed = urlsplit(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTML derivative requires an absolute HTTP(S) source URL")
    html_title, blocks = _visible_blocks(content)
    visible_character_count = sum(len(text) for _, text in blocks)
    if visible_character_count < 1:
        raise ValueError("Validated HTML contains no visible text")

    title = _clean_text(document_title or html_title or "Archived web document")
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Trading AI document acquisition",
        subject="Offline PDF derivative of validated publisher HTML",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "HtmlPdfTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        spaceAfter=10,
    )
    metadata_style = ParagraphStyle(
        "HtmlPdfMetadata",
        parent=styles["BodyText"],
        alignment=TA_LEFT,
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor="#555555",
        spaceAfter=5,
    )
    heading_style = ParagraphStyle(
        "HtmlPdfHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "HtmlPdfBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        spaceAfter=5,
    )
    list_style = ParagraphStyle(
        "HtmlPdfList",
        parent=body_style,
        leftIndent=10,
        firstLineIndent=-7,
    )

    story = [
        Paragraph(escape(title), title_style),
        Paragraph(f"<b>Source:</b> {escape(source_url)}", metadata_style),
        Paragraph(
            "Generated from validated publisher HTML. The exact original HTML is retained separately.",
            metadata_style,
        ),
        Spacer(1, 4 * mm),
    ]
    for kind, text in blocks:
        style = heading_style if kind == "heading" else list_style if kind == "list" else body_style
        prefix = "&#8226; " if kind == "list" else ""
        for position, chunk in enumerate(_paragraphs(text)):
            rendered = escape(chunk).replace("\n", "<br/>")
            story.append(Paragraph((prefix if position == 0 else "") + rendered, style))
    def canvas_factory(*args: object, **kwargs: object) -> Canvas:
        kwargs["invariant"] = 1
        return Canvas(*args, **kwargs)

    document.build(story, canvasmaker=canvas_factory)
    body = output.getvalue()
    if not body.startswith(b"%PDF-"):
        raise RuntimeError("HTML renderer did not produce a PDF")
    return HtmlPdfRender(
        body=body,
        title=title,
        visible_character_count=visible_character_count,
        block_count=len(blocks),
    )
