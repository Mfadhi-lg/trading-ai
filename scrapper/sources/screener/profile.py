"""Source-specific Screener profile parsing."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag
from ...core.nums import clean_text
from .models import SourceField, CompanyProfileLink, CompanyTextObservation
from .parsing import _node_text


def _parse_company_profile(
    soup: BeautifulSoup,
) -> tuple[
    str,
    list[SourceField],
    list[CompanyProfileLink],
    list[CompanyTextObservation],
]:
    """Parse page description, company links and visible About prose.

    The desktop and mobile link groups contain the same targets. Only the
    desktop group is canonical so responsive markup does not duplicate rows.
    About citation links remain separate ordered links while their displayed
    markers also remain in the source-faithful prose.
    """
    top = soup.select_one("#top")
    profile = soup.select_one("#top .company-profile")
    if not isinstance(top, Tag) or not isinstance(profile, Tag):
        raise ValueError("Screener company profile is missing")

    title = profile.find("div", class_="title")
    heading = clean_text(title.get_text(" ", strip=True) if title else "About")
    about = profile.select_one(".about")
    if not isinstance(about, Tag):
        raise ValueError("Screener About text is missing")

    fields: list[SourceField] = []
    links: list[CompanyProfileLink] = []
    texts: list[CompanyTextObservation] = []

    description = soup.select_one('meta[name="description"]')
    description_text = (
        clean_text(str(description.get("content", "")))
        if isinstance(description, Tag)
        else ""
    )
    if description_text:
        fields.append(
            SourceField(
                position=len(fields),
                key_raw="Description",
                raw_text=description_text,
                display_text=description_text,
                role="company_description",
                visibility_state="metadata",
            )
        )
        texts.append(
            CompanyTextObservation(
                position=0,
                text_type="company_description",
                heading_raw="Description",
                display_text=description_text,
                content_origin="source_metadata",
                is_source_generated=False,
            )
        )

    desktop_links = top.select_one(".company-links.show-from-tablet-landscape")
    # SME pages can omit this desktop-only group entirely.  The exchange
    # identifier is still parsed independently from #company-info, and the
    # absence of an optional profile-link group must not discard an otherwise
    # complete company page.
    profile_anchors = (
        desktop_links.find_all("a", href=True, recursive=False)
        if isinstance(desktop_links, Tag)
        else []
    )
    for anchor in profile_anchors:
        raw_text, label = _node_text(anchor)
        upper = label.upper()
        if upper.startswith("BSE:"):
            link_type = "bse"
            identifier = label.split(":", 1)[1].strip()
        elif upper.startswith("NSE:"):
            link_type = "nse"
            identifier = label.split(":", 1)[1].strip().upper()
        else:
            link_type = "website"
            identifier = None
        url = str(anchor["href"])
        fields.append(
            SourceField(
                position=len(fields),
                key_raw=link_type.upper(),
                raw_text=raw_text,
                display_text=label,
                role=f"company_link_{link_type}",
                link_url=url,
            )
        )
        links.append(
            CompanyProfileLink(
                position=len(links),
                link_type=link_type,
                label_raw=label,
                identifier_value=identifier,
                source_url=url,
            )
        )

    about_raw, about_text = _node_text(about)
    fields.append(
        SourceField(
            position=len(fields),
            key_raw=heading,
            raw_text=about_raw,
            display_text=about_text,
            role="about_text",
        )
    )
    texts.append(
        CompanyTextObservation(
            position=0,
            text_type="about",
            heading_raw=heading,
            display_text=about_text,
            content_origin="source_visible_text",
            is_source_generated=False,
        )
    )
    for anchor in about.find_all("a", href=True):
        raw_text, label = _node_text(anchor)
        url = str(anchor["href"])
        fields.append(
            SourceField(
                position=len(fields),
                key_raw="About Source",
                raw_text=raw_text,
                display_text=label,
                role="about_source_link",
                link_url=url,
            )
        )
        links.append(
            CompanyProfileLink(
                position=len(links),
                link_type="about_source",
                label_raw=label,
                identifier_value=None,
                source_url=url,
            )
        )

    # The About prose is the captured artifact; its inline source hyperlink and
    # the company website link are recorded when the source renders them but are
    # not required -- many companies carry neither, and discarding a complete
    # page over a missing anchor loses real financial data. Only an exchange
    # The profile link is useful evidence but is not required: identity may be
    # available in #company-info metadata, and some SME/ETF pages omit the
    # visible exchange link while still carrying usable financial data.
    return heading, fields, links, texts

# Preserve the legacy facade identity for existing serialized callables.
_parse_company_profile.__module__ = "scrapper.sources.screener.company"
