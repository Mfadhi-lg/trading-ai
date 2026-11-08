"""Source-specific Screener commentary parsing."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag
from ...core.nums import clean_text
from .models import SourceField, CompanyTextObservation
from .parsing import _node_text


def parse_commentary_fragment(
    content: bytes,
) -> list[CompanyTextObservation]:
    """Parse the wiki/commentary modal HTML into text observations.

    The fragment is fetched from ``/wiki/company/{id}/commentary/v2/`` and
    contains rich HTML with sourced citations. It typically contains two
    sections: 'About' and 'Key Points'.

    Returns a list of ``CompanyTextObservation`` objects.
    """
    soup = BeautifulSoup(content, "html.parser")
    observations: list[CompanyTextObservation] = []
    
    sub_divs = soup.find_all("div", class_="sub")
    for index, sub_div in enumerate(sub_divs):
        # Find preceding header
        header_text = ""
        prev = sub_div.find_previous(class_=lambda x: x and "strong" in x)
        if prev:
            # Make a copy to avoid mutating the original soup
            header_node = BeautifulSoup(str(prev), "html.parser").find(class_=lambda x: x and "strong" in x)
            if header_node:
                for span in header_node.find_all("span"):
                    span.decompose()
                header_text = clean_text(header_node.get_text(" ", strip=True))
        
        if not header_text:
            header_text = "Key Points" if index > 0 else "About"
            
        raw_text, display_text = _node_text(sub_div)
        if not display_text.strip():
            continue
            
        # Map text_type based on header
        text_type = "commentary"
        if "about" in header_text.lower():
            text_type = "commentary_about"
        elif "key points" in header_text.lower():
            text_type = "key_points"
            
        observations.append(
            CompanyTextObservation(
                position=index,
                text_type=text_type,
                heading_raw=header_text,
                display_text=display_text,
                content_origin="source_visible_text",
                is_source_generated=False,
            )
        )
    return observations


def _parse_pros_cons(
    soup: BeautifulSoup,
) -> tuple[str, list[SourceField], list[CompanyTextObservation]]:
    """Parse Pros/Cons as prose and retain Screener's generation disclosure."""
    section = soup.select_one("section#analysis")
    if not isinstance(section, Tag):
        raise ValueError("Screener Pros and Cons section is missing")
    pros = section.select_one(".pros")
    cons = section.select_one(".cons")
    if not isinstance(pros, Tag) or not isinstance(cons, Tag):
        raise ValueError("Screener Pros or Cons list is missing")

    disclosure = section.select_one("p.sub .font-weight-500")
    disclosure_raw, disclosure_text = _node_text(
        disclosure if isinstance(disclosure, Tag) else None
    )
    source_generated = "machine generated" in disclosure_text.lower()

    fields: list[SourceField] = []
    texts: list[CompanyTextObservation] = []
    for container, text_type in ((pros, "pro"), (cons, "con")):
        heading_node = container.select_one(".title")
        heading = clean_text(
            heading_node.get_text(" ", strip=True) if heading_node else text_type.title()
        )
        if isinstance(heading_node, Tag):
            heading_raw, heading_display = _node_text(heading_node)
            fields.append(
                SourceField(
                    position=len(fields),
                    key_raw=heading,
                    raw_text=heading_raw,
                    display_text=heading_display,
                    role="commentary_heading",
                )
            )
        for position, item in enumerate(container.select("ul > li")):
            raw_text, display_text = _node_text(item)
            fields.append(
                SourceField(
                    position=len(fields),
                    key_raw=heading,
                    raw_text=raw_text,
                    display_text=display_text,
                    role=(
                        f"{text_type}_source_generated_commentary"
                        if source_generated
                        else f"{text_type}_commentary"
                    ),
                )
            )
            texts.append(
                CompanyTextObservation(
                    position=position,
                    text_type=text_type,
                    heading_raw=heading,
                    display_text=display_text,
                    content_origin=(
                        "source_generated_commentary"
                        if source_generated
                        else "source_commentary_unverified"
                    ),
                    is_source_generated=source_generated,
                )
            )

    # Either source list -- or both -- may legitimately be empty: the section
    # renders with its headings and no items. Preserve both headings and every
    # displayed item. Emptiness is reported by the non-blocking
    # pros_cons_text_present rule rather than discarding the whole page.

    fields.append(
        SourceField(
            position=len(fields),
            key_raw="Disclosure",
            raw_text=disclosure_raw,
            display_text=disclosure_text,
            role="commentary_generation_disclosure",
        )
    )
    texts.append(
        CompanyTextObservation(
            position=0,
            text_type="commentary_disclosure",
            heading_raw="Disclosure",
            display_text=disclosure_text,
            content_origin="source_visible_text",
            is_source_generated=False,
        )
    )

    tooltip = section.select_one(".tooltip")
    if isinstance(tooltip, Tag):
        tooltip_raw, tooltip_text = _node_text(tooltip)
        fields.append(
            SourceField(
                position=len(fields),
                key_raw="Methodology",
                raw_text=tooltip_raw,
                display_text=tooltip_text,
                role="commentary_methodology",
                visibility_state="conditional",
            )
        )
        texts.append(
            CompanyTextObservation(
                position=0,
                text_type="commentary_methodology",
                heading_raw="Methodology",
                display_text=tooltip_text,
                content_origin="source_conditional_text",
                is_source_generated=False,
            )
        )
    return "Pros and Cons", fields, texts

# Preserve the legacy facade identity for existing serialized callables.
parse_commentary_fragment.__module__ = "scrapper.sources.screener.company"
_parse_pros_cons.__module__ = "scrapper.sources.screener.company"
