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


from .download.derivatives import HTML_PDF_RENDERER
from .download.derivatives import HTML_PDF_RENDERER_VERSION
from .download.derivatives import HTML_PDF_MEDIA_TYPE

from .download.derivatives import _BLOCK_TAGS
from .download.derivatives import _REMOVED_TAGS


from .download.derivatives import HtmlPdfRender


def _clean_text(value: str) -> str:
    """Compatible entry point for the extracted owner."""
    from .download.derivatives import _clean_text as _owner
    return _owner(value)


def _visible_blocks(content: bytes) -> tuple[str, list[tuple[str, str]]]:
    """Compatible entry point for the extracted owner."""
    from .download.derivatives import _visible_blocks as _owner
    return _owner(
        content,
        _BLOCK_TAGS=_BLOCK_TAGS,
        _REMOVED_TAGS=_REMOVED_TAGS,
        _clean_text=_clean_text,
    )


def _paragraphs(text: str, *, chunk_size: int = 3500) -> list[str]:
    """Compatible entry point for the extracted owner."""
    from .download.derivatives import _paragraphs as _owner
    return _owner(text, chunk_size=chunk_size)


def render_validated_html_to_pdf(
    content: bytes,
    *,
    source_url: str,
    document_title: str | None = None,
) -> HtmlPdfRender:
    """Compatible entry point for the extracted owner."""
    from .download.derivatives import render_validated_html_to_pdf as _owner
    return _owner(
        content,
        source_url=source_url,
        document_title=document_title,
        _clean_text=_clean_text,
        _paragraphs=_paragraphs,
        _visible_blocks=_visible_blocks,
    )
