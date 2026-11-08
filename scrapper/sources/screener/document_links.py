from __future__ import annotations
import re
from bs4 import BeautifulSoup, NavigableString, Tag
from ...core.nums import clean_text
from .models import SourceField, DocumentLink
from .parsing import _node_text


CONCALL_LINK_TYPES = {
    "transcript": "concall_transcript",
    "ai summary": "concall_notes",
    "ppt": "concall_ppt",
    "rec": "concall_recording",
}


def _direct_text(node: Tag) -> str:
    """Text of a node's own string children only, ignoring nested elements.

    A Screener document anchor holds its title as direct text and its date or
    publisher as a nested ``.ink-600`` element; this keeps the two separate
    without a string-subtraction heuristic.
    """
    return clean_text(
        "".join(str(child) for child in node.children if isinstance(child, NavigableString))
    )


def _split_publisher(meta_text: str) -> tuple[str | None, str | None]:
    """Split a `<date> from <publisher>` meta line into (period, publisher).

    Screener renders annual-report and credit-rating meta as ``from bse`` or
    ``3 Jul from care``. The publisher is the source's own claim, never treated
    as the actual document publisher. When no ``from`` clause is present the
    whole text is the period label and no publisher is claimed.
    """
    match = re.search(r"\bfrom\s+(.+)$", meta_text, flags=re.IGNORECASE)
    if not match:
        cleaned = clean_text(meta_text)
        return (cleaned or None), None
    period = clean_text(meta_text[: match.start()])
    return (period or None), clean_text(match.group(1)) or None


def _parse_documents(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceField], list[DocumentLink]]:
    """Parse the Documents section link lists (announcements, annual reports,
    credit ratings and concalls) as metadata only.

    Phase 1 records source titles, periods, publisher claims, positions,
    availability and URLs in source order. No PDF, PPT or recording is
    downloaded. A concall link with no ``href`` (rendered as a bare label) is a
    source-declared unavailable slot, kept distinct from an available link.
    """
    section = soup.select_one("section#documents")
    if not isinstance(section, Tag):
        raise ValueError("Documents section is missing")
    heading_node = section.find("h2")
    heading = clean_text(
        heading_node.get_text(" ", strip=True) if heading_node else "Documents"
    )

    fields: list[SourceField] = []
    links: list[DocumentLink] = []

    def add(
        document_type: str,
        title: str,
        period: str | None,
        publisher: str | None,
        url: str | None,
        source_node: Tag,
    ) -> None:
        position = len(links)
        raw_text, display_text = _node_text(source_node)
        fields.append(
            SourceField(
                position=position,
                key_raw=title,
                raw_text=raw_text,
                display_text=display_text,
                role=document_type,
                link_url=url,
            )
        )
        links.append(
            DocumentLink(
                position=position,
                document_type=document_type,
                title_raw=title,
                period_label_raw=period,
                publisher=publisher,
                source_url=url,
                availability_status="available" if url else "unavailable",
            )
        )

    announcements = section.select_one("#company-announcements-tab")
    if isinstance(announcements, Tag):
        for li in announcements.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            meta = li.find(class_="ink-600")
            period = clean_text(meta.get_text(" ", strip=True)) if isinstance(meta, Tag) else None
            add(
                "announcement",
                _direct_text(anchor),
                period,
                None,
                str(anchor["href"]),
                anchor,
            )

    annual_reports = section.select_one("div.annual-reports")
    if isinstance(annual_reports, Tag):
        for li in annual_reports.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            title = _direct_text(anchor)
            meta = li.find(class_="ink-600")
            _, publisher = (
                _split_publisher(clean_text(meta.get_text(" ", strip=True)))
                if isinstance(meta, Tag)
                else (None, None)
            )
            add(
                "annual_report",
                title,
                title,
                publisher,
                str(anchor["href"]),
                anchor,
            )
        # The DRHP button is a distinct prospectus link below the report list.
        for button in annual_reports.select("a.button[href]"):
            add(
                "drhp",
                _direct_text(button),
                None,
                None,
                str(button["href"]),
                button,
            )

    credit_ratings = section.select_one("div.credit-ratings")
    if isinstance(credit_ratings, Tag):
        for li in credit_ratings.select("ul.list-links > li"):
            anchor = li.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            meta = li.find(class_="ink-600")
            period, publisher = (
                _split_publisher(clean_text(meta.get_text(" ", strip=True)))
                if isinstance(meta, Tag)
                else (None, None)
            )
            add(
                "credit_rating",
                _direct_text(anchor),
                period,
                publisher,
                str(anchor["href"]),
                anchor,
            )

    concalls = section.select_one("div.concalls")
    if isinstance(concalls, Tag):
        for li in concalls.select("ul.list-links > li"):
            period_node = li.find("div", class_="nowrap")
            period = (
                clean_text(period_node.get_text(" ", strip=True))
                if isinstance(period_node, Tag)
                else None
            )
            # Screener replaced concall-link with button-chip in October 2026.
            # Select the union once: mixed markup must not duplicate links, and
            # bare chips still represent unavailable slots.
            for element in li.select('.concall-link, .button-chip'):
                label = _direct_text(element)
                document_type = CONCALL_LINK_TYPES.get(label.lower(), "concall_other")
                # An anchor carries an href; the AI Summary button carries a
                # Screener modal endpoint; a bare label element has neither.
                url = element.get("href") or element.get("data-url")
                url = str(url) if url else None
                publisher = "screener" if document_type == "concall_notes" and url else None
                add(document_type, label, period, publisher, url, element)

    if not links:
        raise ValueError("Documents section has no links")
    return heading, fields, links

# Preserve the legacy facade identity for serialized callables.
_direct_text.__module__ = "scrapper.sources.screener.company"
_split_publisher.__module__ = "scrapper.sources.screener.company"
_parse_documents.__module__ = "scrapper.sources.screener.company"
