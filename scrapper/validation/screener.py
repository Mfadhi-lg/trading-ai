"""Validation and automatic-acceptance policy for Screener artifacts."""

from __future__ import annotations
from ..db.repository_backend import statement, is_postgresql, execute, generated_id, atomic_repository, begin_immediate

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from ..sources.screener.manifest import audit_screener_fixture_coverage, shareholding_unavailable


from .screener_rules import (
    peer_fragment_rules,
    identity_rules,
    financial_rules,
    growth_rules,
    shareholding_rules,
    profile_rules,
    document_rules,
    metric_review_rules,
    peer_supplement_rules,
)


from ..db.screener import acceptance as _acceptance


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _source_has_shareholding_warning(content: bytes) -> bool:
    """True when the saved response carries the shareholding classification note.

    Screener writes this footnote to explain FII/DII movements across the
    Sep-2022 XBRL change, so a company whose table has no institutional
    categories never gets one. Mirroring the parser's own selector keeps this a
    capture check: it fires when the source has the note and the parse lost it,
    and stays quiet when the source has none to give.
    """
    section = BeautifulSoup(content, "html.parser").select_one("section#shareholding")
    return bool(section and section.select_one("p.sub.small"))


def _source_lists_annual_report(content: bytes) -> bool:
    """True when the saved response actually lists at least one annual report.

    Screener renders the Annual reports box for every company but leaves it
    empty until the first report is filed, so a company a few quarters past
    listing has none. Reading the source list keeps the capture requirement
    honest: it still fires when the source has reports and the parse dropped
    them, and stays quiet when the company genuinely has none.
    """
    box = BeautifulSoup(content, "html.parser").select_one(
        "section#documents div.annual-reports"
    )
    return bool(box and box.select("ul.list-links > li"))


def _balance_sheet_imbalances(
    conn: sqlite3.Connection, parse_id: int
) -> tuple[int, str | None]:
    """Count balance-sheet periods where Total Assets and Total Liabilities
    disagree beyond display-rounding tolerance.

    Screener rounds each total to whole crore, so a 1% (min 1 crore) tolerance
    absorbs rounding while still catching a real imbalance or a scale error.
    """
    rows = conn.execute(
        """SELECT column_position, line_item_code, value_decimal
           FROM financial_observations
           WHERE parse_id=? AND section_code='balance_sheet'
             AND line_item_code IN ('total_assets', 'total_liabilities')""",
        (parse_id,),
    ).fetchall()
    by_period: dict[int, dict[str, Decimal]] = {}
    for row in rows:
        if row["value_decimal"] is None:
            continue
        by_period.setdefault(row["column_position"], {})[row["line_item_code"]] = Decimal(
            row["value_decimal"]
        )
    imbalanced: list[str] = []
    for column_position, sides in sorted(by_period.items()):
        assets = sides.get("total_assets")
        liabilities = sides.get("total_liabilities")
        if assets is None or liabilities is None:
            continue
        tolerance = max(Decimal("1"), abs(assets) * Decimal("0.01"))
        if abs(assets - liabilities) > tolerance:
            imbalanced.append(f"col{column_position}:{assets}!={liabilities}")
    return len(imbalanced), "; ".join(imbalanced) if imbalanced else None


def _shareholding_total_anomalies(
    conn: sqlite3.Connection, parse_id: int, frequency: str
) -> tuple[int, str | None]:
    """Count complete shareholding periods whose holder-category percentages do
    not total ~100.

    Only percentage rows (holder categories) are summed; the `No. of
    Shareholders` COUNT row is excluded. A period is checked only when every
    category value is present. Screener rounds each category to two decimals, so
    a 0.1 percentage-point tolerance absorbs rounding while catching a real gap.
    """
    category_rows = conn.execute(
        """SELECT COUNT(DISTINCT row_position) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency=? AND unit_code='PERCENT_POINT'""",
        (parse_id, frequency),
    ).fetchone()["n"]
    rows = conn.execute(
        """SELECT column_position, period_label_raw, value_decimal
           FROM shareholding_observations
           WHERE parse_id=? AND frequency=? AND unit_code='PERCENT_POINT'""",
        (parse_id, frequency),
    ).fetchall()
    by_period: dict[int, list] = {}
    labels: dict[int, str] = {}
    for row in rows:
        if row["value_decimal"] is None:
            continue
        by_period.setdefault(row["column_position"], []).append(
            Decimal(row["value_decimal"])
        )
        labels[row["column_position"]] = row["period_label_raw"]
    anomalies: list[str] = []
    tolerance = Decimal("0.1")
    for column_position, values in sorted(by_period.items()):
        if len(values) != category_rows:
            continue  # incomplete period: not validated
        total = sum(values, Decimal("0"))
        if abs(total - Decimal("100")) > tolerance:
            anomalies.append(f"{labels[column_position]}:{total}")
    return len(anomalies), "; ".join(anomalies) if anomalies else None


@atomic_repository('validation')
def validate_peer_parse(
    conn: sqlite3.Connection,
    parse_id: int,
    expected_source_company_id: str | None,
    expected_symbol: str,
) -> str:
    """Validate the independently saved peer fragment before it can supplement
    an accepted company page.
    """
    source_cell_count = conn.execute(
        """SELECT COUNT(*) AS n FROM source_table_cells stc
           JOIN source_sections ss ON ss.section_id=stc.section_id
           WHERE ss.parse_id=? AND ss.section_code='peer_table'""",
        (parse_id,),
    ).fetchone()["n"]
    observation_count = conn.execute(
        "SELECT COUNT(*) AS n FROM peer_observations WHERE parse_id=?",
        (parse_id,),
    ).fetchone()["n"]
    company_count = conn.execute(
        """SELECT COUNT(DISTINCT peer_source_company_id) AS n
           FROM peer_observations
           WHERE parse_id=? AND row_kind='company'""",
        (parse_id,),
    ).fetchone()["n"]
    numeric_failures = conn.execute(
        """SELECT COUNT(*) AS n FROM peer_observations
           WHERE parse_id=? AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    unknown_metrics = conn.execute(
        statement(conn, """SELECT GROUP_CONCAT(DISTINCT metric_label_raw) AS labels
           FROM peer_observations WHERE parse_id=? AND metric_code IS NULL""", "SELECT STRING_AGG(DISTINCT metric_label_raw::text, ',') AS labels\n           FROM peer_observations WHERE parse_id=? AND metric_code IS NULL"),
        (parse_id,),
    ).fetchone()["labels"]
    placeholder_count = conn.execute(
        """SELECT COUNT(*) AS n FROM source_table_cells stc
           JOIN source_sections ss ON ss.section_id=stc.section_id
           WHERE ss.parse_id=?
             AND LOWER(stc.normalized_display_text) LIKE '%loading peers table%'""",
        (parse_id,),
    ).fetchone()["n"]
    subject = conn.execute(
        """SELECT peer_source_company_id, peer_source_url,
                  COUNT(*) AS metric_count
           FROM peer_observations
           WHERE parse_id=? AND is_subject_company=1
           GROUP BY peer_source_company_id, peer_source_url""",
        (parse_id,),
    ).fetchall()
    subject_ok = (
        len(subject) == 1
        and subject[0]["peer_source_company_id"] == expected_source_company_id
        and "/company/" in (subject[0]["peer_source_url"] or "")
        and int(subject[0]["metric_count"]) > 0
    )
    rules = (
        peer_fragment_rules(
            company_count=company_count,
            expected_source_company_id=expected_source_company_id,
            expected_symbol=expected_symbol,
            numeric_failures=numeric_failures,
            observation_count=observation_count,
            placeholder_count=placeholder_count,
            source_cell_count=source_cell_count,
            subject=subject,
            subject_ok=subject_ok,
            unknown_metrics=unknown_metrics,
        )
    )
    created = _utc_now()
    with conn:
        for code, severity, passed, expected, actual in rules:
            conn.execute(
                """INSERT INTO validation_results
                   (parse_id, rule_code, severity, status, expected_value,
                    actual_value, details_json, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', ?)""",
                (
                    parse_id, code, severity, "passed" if passed else "failed",
                    expected, actual, created,
                ),
            )
        hard_fail = any(not passed and severity == "error" for _, severity, passed, _, _ in rules)
        manual = any(not passed and severity == "manual_review" for _, severity, passed, _, _ in rules)
        status = "failed" if hard_fail else "passed_with_warnings" if manual else "passed"
        conn.execute(
            "UPDATE parse_artifacts SET validation_status=? WHERE parse_id=?",
            (status, parse_id),
        )
    return status


@atomic_repository('validation')
def validate_parse(
    conn: sqlite3.Connection,
    parse_id: int,
    expected_symbol: str,
    expected_scope: str,
) -> str:
    blob = conn.execute(
        """SELECT rb.local_path FROM parse_artifacts pa
           JOIN response_blobs rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    content = Path(blob["local_path"]).read_bytes() if blob is not None else None
    manifest_errors = (
        audit_screener_fixture_coverage(content)
        if content is not None
        else ["parse artifact has no response blob"]
    )
    # Without a response to read, keep the stricter requirement: a missing blob
    # is already a blocking failure and must not silently relax another rule.
    source_lists_annual_report = (
        _source_lists_annual_report(content) if content is not None else True
    )
    source_has_shareholding_warning = (
        _source_has_shareholding_warning(content) if content is not None else True
    )
    source_soup = BeautifulSoup(content, "html.parser") if content is not None else None
    unavailable_frequencies = {
        frequency for frequency in ("quarterly", "yearly")
        if source_soup is not None and shareholding_unavailable(source_soup, frequency)
    }
    actual_identifiers = {
        str(row["normalized_display_text"])
        for row in conn.execute(
        """SELECT sf.normalized_display_text
           FROM source_fields sf JOIN source_sections ss ON ss.section_id=sf.section_id
           WHERE ss.parse_id=? AND ss.section_code='identity'
             AND sf.field_key_raw IN ('NSE', 'BSE')
             AND sf.normalized_display_text != ''""",
        (parse_id,),
    )}
    scope = conn.execute(
        "SELECT reporting_scope FROM company_metric_observations WHERE parse_id=? LIMIT 1",
        (parse_id,),
    ).fetchone()
    metric_count = conn.execute(
        "SELECT COUNT(*) AS n FROM company_metric_observations WHERE parse_id=?", (parse_id,)
    ).fetchone()["n"]
    financial_count = conn.execute(
        "SELECT COUNT(*) AS n FROM financial_observations WHERE parse_id=?", (parse_id,)
    ).fetchone()["n"]
    numeric_failures = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    unknown_metrics = conn.execute(
        statement(conn, """SELECT GROUP_CONCAT(label_raw) AS labels FROM company_metric_observations
           WHERE parse_id=? AND metric_code IS NULL""", "SELECT STRING_AGG(label_raw::text, ',') AS labels FROM company_metric_observations\n           WHERE parse_id=? AND metric_code IS NULL"),
        (parse_id,),
    ).fetchone()["labels"]
    pnl_count = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='pnl'""",
        (parse_id,),
    ).fetchone()["n"]
    # A P&L column must carry exactly one period type, and a TTM column must
    # never be assigned a fiscal year: annual and TTM periods stay distinct.
    pnl_mixed_columns = conn.execute(
        """SELECT COUNT(*) AS n FROM (
               SELECT column_position FROM financial_observations
               WHERE parse_id=? AND section_code='pnl'
               GROUP BY column_position
               HAVING COUNT(DISTINCT period_type) > 1
           )""",
        (parse_id,),
    ).fetchone()["n"]
    pnl_ttm_with_fiscal_year = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='pnl'
             AND period_type='ttm' AND fiscal_year IS NOT NULL""",
        (parse_id,),
    ).fetchone()["n"]
    pnl_period_collisions = int(pnl_mixed_columns) + int(pnl_ttm_with_fiscal_year)

    balance_count = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='balance_sheet'""",
        (parse_id,),
    ).fetchone()["n"]
    balance_imbalance_count, balance_imbalance_detail = _balance_sheet_imbalances(
        conn, parse_id
    )

    cash_count = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='cash_flow'""",
        (parse_id,),
    ).fetchone()["n"]

    ratios_count = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='ratios'""",
        (parse_id,),
    ).fetchone()["n"]
    # A present ratio value must be typed; missing cells stay missing.
    ratios_untyped = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='ratios'
             AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    # Annual ratios must stay separate from the top snapshot ratio panel: they
    # keep a ratio-specific unit (days/percent/ratio/count, never a crore money
    # amount) and never reuse a top-panel metric code.
    ratios_panel_collisions = conn.execute(
        """SELECT COUNT(*) AS n FROM financial_observations
           WHERE parse_id=? AND section_code='ratios'
             AND (unit_code NOT IN ('DAYS', 'PERCENT_POINT', 'RATIO', 'COUNT')
                  OR line_item_code IN (
                      SELECT metric_code FROM company_metric_observations
                      WHERE parse_id=? AND metric_code IS NOT NULL))""",
        (parse_id, parse_id),
    ).fetchone()["n"]

    growth_count = conn.execute(
        "SELECT COUNT(*) AS n FROM growth_observations WHERE parse_id=?", (parse_id,)
    ).fetchone()["n"]
    # A present numeric growth value must be typed; an unavailable one (bare "%"
    # or blank) must stay missing and must never be stored as zero.
    growth_untyped = conn.execute(
        """SELECT COUNT(*) AS n FROM growth_observations
           WHERE parse_id=? AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    growth_missing_as_value = conn.execute(
        """SELECT COUNT(*) AS n FROM growth_observations
           WHERE parse_id=? AND missing_kind IS NOT NULL AND value_decimal IS NOT NULL""",
        (parse_id,),
    ).fetchone()["n"]

    shareholding_count = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='quarterly'""",
        (parse_id,),
    ).fetchone()["n"]
    # A present shareholding value must be typed; missing cells stay missing.
    shareholding_untyped = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='quarterly'
             AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    # Every quarterly row must carry a valid per-period regime tag; a single row
    # must NEVER be labelled `cross_regime` (that is a derived two-observation
    # comparison, not a row property).
    shareholding_bad_regime = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='quarterly'
             AND classification_regime NOT IN
                 ('pre_sep_2022', 'xbrl_sep_2022_plus', 'unknown')""",
        (parse_id,),
    ).fetchone()["n"]
    shareholding_warning_present = conn.execute(
        """SELECT COUNT(*) AS n FROM source_fields sf
           JOIN source_sections ss ON ss.section_id=sf.section_id
           WHERE ss.parse_id=? AND ss.section_code='shareholding_quarterly'
             AND sf.field_role='classification_warning'""",
        (parse_id,),
    ).fetchone()["n"]
    # The note is only owed when the source actually carries it. A company whose
    # table has no institutional categories never gets one, and rejecting it for
    # an absence the source itself declares would discard complete data.
    shareholding_warning_captured = (
        shareholding_warning_present > 0 or not source_has_shareholding_warning
    )
    shareholding_total_anomalies, shareholding_total_detail = _shareholding_total_anomalies(
        conn, parse_id, "quarterly"
    )

    # Yearly shareholding (S10). Parsed independently of the quarterly table and
    # distinguished by `frequency`, so the two coexist without overwriting one
    # another. The same guarantees apply: present, typed, per-period regime tags
    # only (never `cross_regime` on a row), and category totals ~100.
    yearly_count = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='yearly'""",
        (parse_id,),
    ).fetchone()["n"]
    yearly_untyped = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='yearly'
             AND missing_kind IS NULL AND value_decimal IS NULL""",
        (parse_id,),
    ).fetchone()["n"]
    yearly_bad_regime = conn.execute(
        """SELECT COUNT(*) AS n FROM shareholding_observations
           WHERE parse_id=? AND frequency='yearly'
             AND classification_regime NOT IN
                 ('pre_sep_2022', 'xbrl_sep_2022_plus', 'unknown')""",
        (parse_id,),
    ).fetchone()["n"]
    yearly_total_anomalies, yearly_total_detail = _shareholding_total_anomalies(
        conn, parse_id, "yearly"
    )

    # Company profile prose and links (S12). These observations deliberately
    # carry no numeric value columns: percentages embedded in commentary remain
    # untrusted source text. Pros/Cons are accepted as source-generated only
    # when the page's own machine-generation disclosure is present.
    text_types = {
        str(row["text_type"]): int(row["n"])
        for row in conn.execute(
            """SELECT text_type, COUNT(*) AS n FROM company_text_observations
               WHERE parse_id=? GROUP BY text_type""",
            (parse_id,),
        )
    }
    about_text_present = (
        text_types.get("company_description", 0) == 1
        and text_types.get("about", 0) == 1
    )
    link_types = {
        str(row["link_type"])
        for row in conn.execute(
            "SELECT link_type FROM company_profile_links WHERE parse_id=?",
            (parse_id,),
        )
    }
    # Website and about-source anchors are captured when present but are not
    # required; only an exchange identifier is, because identity resolves
    # through it. See _parse_company_profile.
    required_profile_links = {"bse|nse"}
    profile_links_present = bool({"bse", "nse"} & link_types)
    analysis_headings = {
        str(row["normalized_display_text"])
        for row in conn.execute(
            """SELECT sf.normalized_display_text
               FROM source_fields sf
               JOIN source_sections ss ON ss.section_id=sf.section_id
               WHERE ss.parse_id=? AND ss.section_code='pros_cons'
                 AND sf.field_role='commentary_heading'""",
            (parse_id,),
        )
    }
    commentary_count = text_types.get("pro", 0) + text_types.get("con", 0)
    pros_cons_present = {"Pros", "Cons"} <= analysis_headings and commentary_count > 0
    commentary_not_marked_generated = conn.execute(
        """SELECT COUNT(*) AS n FROM company_text_observations
           WHERE parse_id=? AND text_type IN ('pro', 'con')
             AND (content_origin != 'source_generated_commentary'
                  OR is_source_generated != 1)""",
        (parse_id,),
    ).fetchone()["n"]

    # Document-link metadata (S11). The page lists remain source-faithful fields
    # and each normalized row must keep an explicit available/unavailable state.
    document_stats = conn.execute(
        """SELECT COUNT(*) AS n, MIN(position) AS min_position,
                  MAX(position) AS max_position
           FROM document_links WHERE parse_id=?""",
        (parse_id,),
    ).fetchone()
    document_count = int(document_stats["n"])
    document_positions_contiguous = (
        document_count > 0
        and int(document_stats["min_position"]) == 0
        and int(document_stats["max_position"]) == document_count - 1
    )
    document_source_count = conn.execute(
        """SELECT COUNT(*) AS n FROM source_fields sf
           JOIN source_sections ss ON ss.section_id=sf.section_id
           WHERE ss.parse_id=? AND ss.section_code='document_links'""",
        (parse_id,),
    ).fetchone()["n"]
    document_availability_mismatches = conn.execute(
        """SELECT COUNT(*) AS n FROM document_links
           WHERE parse_id=? AND (
             (availability_status='available' AND source_url IS NULL)
             OR (availability_status='unavailable' AND source_url IS NOT NULL)
           )""",
        (parse_id,),
    ).fetchone()["n"]
    document_families = {
        str(row["document_type"])
        for row in conn.execute(
            "SELECT DISTINCT document_type FROM document_links WHERE parse_id=?",
            (parse_id,),
        )
    }
    # Credit ratings and Concalls are conditional at source: either whole list can
    # be absent when Screener has no corresponding artifact for the company. When
    # present, every link is retained and checked by the known-type rule below. Do
    # not manufacture an unavailable row for an absent list.
    #
    # Annual reports are not mandatory (waived) because not all companies
    # have them published or available.
    required_document_families = {"announcement"}
    required_document_families_present = (
        required_document_families <= document_families
    )
    known_document_types = {
        "announcement",
        "annual_report",
        "credit_rating",
        "drhp",
        "concall_transcript",
        "concall_notes",
        "concall_ppt",
        "concall_recording",
    }
    unknown_document_types = sorted(document_families - known_document_types)

    peer_link = conn.execute(
        """SELECT spl.supplemental_parse_id, pa.parse_status,
                  pa.validation_status
           FROM supplemental_parse_links spl
           JOIN parse_artifacts pa ON pa.parse_id=spl.supplemental_parse_id
           WHERE spl.parent_parse_id=? AND spl.component_code='peer_table'""",
        (parse_id,),
    ).fetchone()
    peer_parse_id = int(peer_link["supplemental_parse_id"]) if peer_link else None
    peer_observation_count = 0
    peer_placeholder_count = 0
    peer_untyped = 0
    peer_subject_count = 0
    if peer_parse_id is not None:
        peer_observation_count = conn.execute(
            "SELECT COUNT(*) AS n FROM peer_observations WHERE parse_id=?",
            (peer_parse_id,),
        ).fetchone()["n"]
        peer_untyped = conn.execute(
            """SELECT COUNT(*) AS n FROM peer_observations
               WHERE parse_id=? AND missing_kind IS NULL AND value_decimal IS NULL""",
            (peer_parse_id,),
        ).fetchone()["n"]
        peer_subject_count = conn.execute(
            """SELECT COUNT(DISTINCT peer_source_company_id) AS n
               FROM peer_observations
               WHERE parse_id=? AND is_subject_company=1""",
            (peer_parse_id,),
        ).fetchone()["n"]
        peer_placeholder_count = conn.execute(
            """SELECT COUNT(*) AS n FROM source_table_cells stc
               JOIN source_sections ss ON ss.section_id=stc.section_id
               WHERE ss.parse_id=?
                 AND LOWER(stc.normalized_display_text)
                     LIKE '%loading peers table%'""",
            (peer_parse_id,),
        ).fetchone()["n"]
    peer_supplement_ok = bool(
        peer_link
        and peer_link["parse_status"] == "ok"
        and peer_link["validation_status"] in {"passed", "passed_with_warnings"}
    )

    rules = (
        identity_rules(
            actual_identifiers=actual_identifiers,
            expected_scope=expected_scope,
            expected_symbol=expected_symbol,
            manifest_errors=manifest_errors,
            scope=scope,
        )
        + financial_rules(
            balance_count=balance_count,
            balance_imbalance_count=balance_imbalance_count,
            balance_imbalance_detail=balance_imbalance_detail,
            cash_count=cash_count,
            financial_count=financial_count,
            metric_count=metric_count,
            numeric_failures=numeric_failures,
            pnl_count=pnl_count,
            pnl_period_collisions=pnl_period_collisions,
            ratios_count=ratios_count,
            ratios_panel_collisions=ratios_panel_collisions,
            ratios_untyped=ratios_untyped,
        )
        + growth_rules(
            growth_count=growth_count,
            growth_missing_as_value=growth_missing_as_value,
            growth_untyped=growth_untyped,
        )
        + shareholding_rules(
            shareholding_bad_regime=shareholding_bad_regime,
            shareholding_count=shareholding_count,
            shareholding_total_anomalies=shareholding_total_anomalies,
            shareholding_total_detail=shareholding_total_detail,
            shareholding_untyped=shareholding_untyped,
            shareholding_warning_captured=shareholding_warning_captured,
            shareholding_warning_present=shareholding_warning_present,
            source_has_shareholding_warning=source_has_shareholding_warning,
            unavailable_frequencies=unavailable_frequencies,
            yearly_bad_regime=yearly_bad_regime,
            yearly_count=yearly_count,
            yearly_total_anomalies=yearly_total_anomalies,
            yearly_total_detail=yearly_total_detail,
            yearly_untyped=yearly_untyped,
        )
        + profile_rules(
            about_text_present=about_text_present,
            analysis_headings=analysis_headings,
            commentary_not_marked_generated=commentary_not_marked_generated,
            link_types=link_types,
            profile_links_present=profile_links_present,
            pros_cons_present=pros_cons_present,
            required_profile_links=required_profile_links,
            text_types=text_types,
        )
        + document_rules(
            document_availability_mismatches=document_availability_mismatches,
            document_count=document_count,
            document_families=document_families,
            document_positions_contiguous=document_positions_contiguous,
            document_source_count=document_source_count,
            document_stats=document_stats,
            required_document_families=required_document_families,
            required_document_families_present=required_document_families_present,
            unknown_document_types=unknown_document_types,
        )
        + metric_review_rules(
            unknown_metrics=unknown_metrics,
        )
        + peer_supplement_rules(
            peer_link=peer_link,
            peer_observation_count=peer_observation_count,
            peer_parse_id=peer_parse_id,
            peer_placeholder_count=peer_placeholder_count,
            peer_subject_count=peer_subject_count,
            peer_supplement_ok=peer_supplement_ok,
            peer_untyped=peer_untyped,
        )
    )
    created = _utc_now()
    with conn:
        for code, severity, passed, expected, actual in rules:
            conn.execute(
                """INSERT INTO validation_results
                   (parse_id, rule_code, severity, status, expected_value,
                    actual_value, details_json, created_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', ?)""",
                (
                    parse_id,
                    code,
                    severity,
                    "passed" if passed else "failed",
                    expected,
                    actual,
                    created,
                ),
            )
        hard_fail = any(
            not passed and severity == "error"
            for _, severity, passed, _, _ in rules
        )
        manual = any(
            not passed and severity == "manual_review"
            for _, severity, passed, _, _ in rules
        )
        warn = any(
            not passed and severity == "warning"
            for _, severity, passed, _, _ in rules
        )
        status = (
            "failed"
            if hard_fail
            else "passed_with_warnings"
            if (manual or warn)
            else "passed"
        )
        conn.execute(
            "UPDATE parse_artifacts SET validation_status=? WHERE parse_id=?",
            (status, parse_id),
        )
    return status


def sync_screener_quarter_state(conn, company_id: int, scope: str) -> None:
    return _acceptance.sync_screener_quarter_state(conn, company_id, scope, _utc_now=_utc_now)


def accept_parse(
    conn: sqlite3.Connection,
    company_id: int,
    parse_id: int,
    source: str,
    scope: str,
    resolved_source_period: str = "not_applicable",
) -> None:
    return _acceptance.accept_parse(conn, company_id, parse_id, source, scope, resolved_source_period, _utc_now=_utc_now, sync_screener_quarter_state=sync_screener_quarter_state)
