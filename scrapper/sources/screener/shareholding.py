"""Source-specific Screener shareholding parsing."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from bs4 import BeautifulSoup, Tag
from ...core.nums import clean_text, decimal_text, decimal_values, missing_kind
from .models import SourceCell, ShareholdingObservation
from .parsing import _node_text, _first_link
from .statements import _canonical_line_label
from .manifest import shareholding_unavailable


# The count row has no `showShareholders(...)` button, so it is mapped by label.
# The four holder-category rows take their canonical code from the button's
# onclick classification token (e.g. `foreign_institutions`), which is the
# source's own classification and more faithful than a label heuristic.
SHAREHOLDING_HOLDER_CODES = {
    "no. of shareholders": "no_of_shareholders",
}


# The XBRL shareholding regime changed from Sep 2022 onwards. Each period is
# tagged from its own period end; the boundary is inclusive of Sep 2022.
SHAREHOLDING_REGIME_BOUNDARY = (2022, 9)


def _shareholding_period_end(label: str) -> str | None:
    """Resolve a `MMM YYYY` shareholding period label to its month-end date."""
    match = re.fullmatch(r"([A-Za-z]{3})\s+(\d{4})", clean_text(label))
    if not match:
        return None
    try:
        month = datetime.strptime(match.group(1), "%b").month
    except ValueError:
        return None
    year = int(match.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day).isoformat()


def _shareholding_regime(period_end: str | None) -> str:
    """Tag one period by the XBRL classification regime it falls in.

    This is a per-period property derived from that period's own end date, never
    a cross-period comparison. The `cross_regime` distinction belongs to a
    derived comparison of two observations (S10) and is never a row's tag.
    """
    if not period_end:
        return "unknown"
    parsed = datetime.strptime(period_end, "%Y-%m-%d").date()
    if (parsed.year, parsed.month) >= SHAREHOLDING_REGIME_BOUNDARY:
        return "xbrl_sep_2022_plus"
    return "pre_sep_2022"


def is_cross_regime_comparison(
    first: ShareholdingObservation, second: ShareholdingObservation
) -> bool:
    """Whether comparing two shareholding observations crosses the Sep-2022 XBRL
    regime boundary.

    This is a DERIVED property of a *pair* of observations and is never stored as
    a tag on a single row (see PLAN.md 4.1). It is True only when both regimes are
    known and differ; an `unknown` regime yields False because the comparison
    cannot be established either way.
    """
    known = {"pre_sep_2022", "xbrl_sep_2022_plus"}
    if (
        first.classification_regime not in known
        or second.classification_regime not in known
    ):
        return False
    return first.classification_regime != second.classification_regime


def _shareholder_code(row_header: Tag, canonical_label: str) -> str | None:
    button = row_header.find("button")
    if isinstance(button, Tag):
        match = re.search(
            r"showShareholders\('([^']*)'", str(button.get("onclick", ""))
        )
        if match:
            return match.group(1)
    return SHAREHOLDING_HOLDER_CODES.get(canonical_label)


def _parse_shareholding_rows(
    table: Tag, frequency: str
) -> tuple[list[SourceCell], list[ShareholdingObservation]]:
    """Parse one shareholding table (quarterly or yearly) into faithful cells
    plus normalized observations.

    The four holder-category rows are percentage points; the `No. of
    Shareholders` row is a COUNT. Each period is regime-tagged from its own end
    date. ``frequency`` is part of every observation's identity, so the quarterly
    and yearly tables can never overwrite one another.
    """
    header_nodes = table.select("thead tr th")
    if len(header_nodes) < 2:
        raise ValueError(f"{frequency.capitalize()} shareholding headers are missing")
    period_labels = [clean_text(node.get_text(" ", strip=True)) for node in header_nodes[1:]]

    cells: list[SourceCell] = []
    for column, node in enumerate(header_nodes):
        raw, display = _node_text(node)
        cells.append(
            SourceCell(0, column, None, display or None, raw, display, "column_header", _first_link(node), int(node.get("rowspan", 1)), int(node.get("colspan", 1)))
        )

    observations: list[ShareholdingObservation] = []
    rows = table.select("tbody > tr")
    for row_position, row in enumerate(rows, start=1):
        row_nodes = row.find_all(["td", "th"], recursive=False)
        if not row_nodes:
            continue
        holder_label = clean_text(row_nodes[0].get_text(" ", strip=True))
        canonical_label = _canonical_line_label(holder_label)
        holder_code = _shareholder_code(row_nodes[0], canonical_label)
        is_category = row_nodes[0].find("button") is not None
        unit_code = "PERCENT_POINT" if is_category else "COUNT"
        for column, node in enumerate(row_nodes):
            raw, display = _node_text(node)
            column_label = (
                "" if column == 0 else period_labels[column - 1] if column - 1 < len(period_labels) else None
            )
            cell_role = (
                ("row_header_expandable" if is_category else "row_header")
                if column == 0
                else "data"
            )
            cells.append(
                SourceCell(
                    row_position, column, holder_label, column_label, raw, display,
                    cell_role, _first_link(node),
                    int(node.get("rowspan", 1)), int(node.get("colspan", 1)),
                )
            )
            if column == 0 or column - 1 >= len(period_labels):
                continue
            period_label = period_labels[column - 1]
            period_end = _shareholding_period_end(period_label)
            values = decimal_values(display)
            observations.append(
                ShareholdingObservation(
                    row=row_position,
                    column=column,
                    frequency=frequency,
                    holder_code=holder_code,
                    holder_label_raw=holder_label,
                    period_label_raw=period_label,
                    period_end=period_end,
                    display_value=display,
                    value_decimal=decimal_text(values[0]) if len(values) == 1 else None,
                    unit_code=unit_code,
                    classification_regime=_shareholding_regime(period_end),
                    missing_kind=missing_kind(display),
                )
            )
    if not observations:
        raise ValueError(f"{frequency.capitalize()} shareholding table has no observations")
    return cells, observations


def _shareholding_table(section: Tag, container_id: str, frequency: str) -> Tag:
    container = section.select_one(f"#{container_id}")
    if not isinstance(container, Tag):
        raise ValueError(f"{frequency.capitalize()} shareholding container is missing")
    table = container.find("table")
    if not isinstance(table, Tag):
        raise ValueError(f"{frequency.capitalize()} shareholding table is missing")
    return table


def _parse_shareholding(
    soup: BeautifulSoup,
) -> tuple[
    str,
    str | None,
    list[SourceCell],
    list[ShareholdingObservation],
    list[SourceCell],
    list[ShareholdingObservation],
]:
    """Parse both the quarterly (S09) and yearly (S10) shareholding tables.

    The two tables are parsed independently and each observation carries a
    distinct ``frequency``, so quarterly and yearly rows coexist without
    overwriting one another. The Sep-2022 XBRL regime tag is a per-period
    property derived from each period's own end date; ``cross_regime`` is never a
    row tag — it is a derived comparison of two observations (see
    :func:`is_cross_regime_comparison`).
    """
    section = soup.select_one("section#shareholding")
    if not isinstance(section, Tag):
        raise ValueError("Shareholding section is missing")
    heading_node = section.find("h2")
    heading = clean_text(
        heading_node.get_text(" ", strip=True) if heading_node else "Shareholding Pattern"
    )
    warning_node = section.select_one("p.sub.small")
    warning = (
        clean_text(warning_node.get_text(" ", strip=True)) if warning_node else None
    )

    q_cells, q_obs = ([], []) if shareholding_unavailable(soup, "quarterly") else _parse_shareholding_rows(
        _shareholding_table(section, "quarterly-shp", "quarterly"), "quarterly"
    )
    y_cells, y_obs = ([], []) if shareholding_unavailable(soup, "yearly") else _parse_shareholding_rows(
        _shareholding_table(section, "yearly-shp", "yearly"), "yearly"
    )
    return heading, warning, q_cells, q_obs, y_cells, y_obs

# Preserve the legacy facade identity for existing serialized callables.
_shareholding_period_end.__module__ = "scrapper.sources.screener.company"
_shareholding_regime.__module__ = "scrapper.sources.screener.company"
is_cross_regime_comparison.__module__ = "scrapper.sources.screener.company"
_shareholder_code.__module__ = "scrapper.sources.screener.company"
_parse_shareholding_rows.__module__ = "scrapper.sources.screener.company"
_shareholding_table.__module__ = "scrapper.sources.screener.company"
_parse_shareholding.__module__ = "scrapper.sources.screener.company"
