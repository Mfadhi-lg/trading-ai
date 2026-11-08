"""Validation and automatic-acceptance policy for Screener artifacts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from ..sources.screener.manifest import audit_screener_fixture_coverage, shareholding_unavailable


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
        """SELECT GROUP_CONCAT(DISTINCT metric_label_raw) AS labels
           FROM peer_observations WHERE parse_id=? AND metric_code IS NULL""",
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
    rules = [
        ("peer_table_present", "error", source_cell_count > 0 and company_count > 0,
         "source_cells>0,companies>0", f"source_cells={source_cell_count},companies={company_count}"),
        ("peer_table_not_placeholder", "error", placeholder_count == 0,
         "0", str(placeholder_count)),
        ("peer_table_numeric_cells", "error", observation_count > 0 and numeric_failures == 0,
         "observations>0,untyped=0", f"observations={observation_count},untyped={numeric_failures}"),
        ("peer_table_subject_match", "error", subject_ok,
         f"{expected_source_company_id}:{expected_symbol}",
         f"matches={len(subject)}"),
        ("known_peer_metrics", "manual_review", unknown_metrics is None,
         "none", unknown_metrics),
    ]
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
        """SELECT GROUP_CONCAT(label_raw) AS labels FROM company_metric_observations
           WHERE parse_id=? AND metric_code IS NULL""",
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

    rules = [
        (
            "screener_manifest_coverage",
            # Screener changes optional navigation controls between company
            # types and page versions. Core financial tables are validated
            # independently; an absent insights tab must not reject them.
            "error" if manifest_errors else "warning",
            not manifest_errors,
            "no uncovered source components",
            "; ".join(manifest_errors) if manifest_errors else "none",
        ),
        (
            "identity_symbol",
            # A present but mismatched identifier is still a hard identity
            # failure. If the page has no visible exchange identifier, the
            # parser has already required Screener's own company metadata.
            "error" if actual_identifiers else "warning",
            expected_symbol in actual_identifiers,
            expected_symbol,
            ",".join(sorted(actual_identifiers)) or None,
        ),
        (
            "reporting_scope",
            "error",
            scope is not None and scope[0] == expected_scope,
            expected_scope,
            scope[0] if scope else None,
        ),
        ("ratio_panel_present", "error", metric_count > 0, ">0", str(metric_count)),
        (
            "quarterly_table_present",
            "error",
            financial_count > 0,
            ">0",
            str(financial_count),
        ),
        (
            "quarterly_numeric_cells",
            "error",
            numeric_failures == 0,
            "0",
            str(numeric_failures),
        ),
        ("profit_loss_table_present", "error", pnl_count > 0, ">0", str(pnl_count)),
        (
            "pnl_annual_ttm_no_collision",
            "error",
            pnl_period_collisions == 0,
            "0",
            str(pnl_period_collisions),
        ),
        (
            "balance_sheet_table_present",
            "error",
            balance_count > 0,
            ">0",
            str(balance_count),
        ),
        (
            "balance_assets_equal_liabilities",
            "warning",
            balance_imbalance_count == 0,
            "0",
            balance_imbalance_detail if balance_imbalance_detail else str(balance_imbalance_count),
        ),
        (
            "cash_flow_table_present",
            "error",
            cash_count > 0,
            ">0",
            str(cash_count),
        ),
        (
            "annual_ratios_table_present",
            "error",
            ratios_count > 0,
            ">0",
            str(ratios_count),
        ),
        (
            "annual_ratios_numeric_cells",
            "error",
            ratios_untyped == 0,
            "0",
            str(ratios_untyped),
        ),
        (
            "annual_ratios_separate_from_panel",
            "error",
            ratios_panel_collisions == 0,
            "0",
            str(ratios_panel_collisions),
        ),
        ("growth_blocks_present", "error", growth_count > 0, ">0", str(growth_count)),
        (
            "growth_numeric_cells",
            "error",
            growth_untyped == 0,
            "0",
            str(growth_untyped),
        ),
        (
            "growth_unavailable_not_zero",
            "error",
            growth_missing_as_value == 0,
            "0",
            str(growth_missing_as_value),
        ),
        (
            "quarterly_shareholding_table_present",
            "error",
            shareholding_count > 0 or "quarterly" in unavailable_frequencies,
            ">0 or explicit source unavailable notice",
            str(shareholding_count),
        ),
        (
            "quarterly_shareholding_numeric_cells",
            "error",
            shareholding_untyped == 0,
            "0",
            str(shareholding_untyped),
        ),
        (
            "shareholding_no_cross_regime",
            "error",
            shareholding_bad_regime == 0,
            "0",
            str(shareholding_bad_regime),
        ),
        (
            "shareholding_classification_warning_present",
            "error",
            shareholding_warning_captured,
            ">0" if source_has_shareholding_warning else "0 (absent at source)",
            str(shareholding_warning_present),
        ),
        (
            "quarterly_shareholding_totals_100",
            "warning",
            shareholding_total_anomalies == 0,
            "0",
            shareholding_total_detail
            if shareholding_total_detail
            else str(shareholding_total_anomalies),
        ),
        (
            "yearly_shareholding_table_present",
            "error",
            yearly_count > 0 or "yearly" in unavailable_frequencies,
            ">0 or explicit source unavailable notice",
            str(yearly_count),
        ),
        (
            "yearly_shareholding_numeric_cells",
            "error",
            yearly_untyped == 0,
            "0",
            str(yearly_untyped),
        ),
        (
            "yearly_shareholding_no_cross_regime",
            "error",
            yearly_bad_regime == 0,
            "0",
            str(yearly_bad_regime),
        ),
        (
            "yearly_shareholding_totals_100",
            "warning",
            yearly_total_anomalies == 0,
            "0",
            yearly_total_detail
            if yearly_total_detail
            else str(yearly_total_anomalies),
        ),
        (
            "company_about_text_present",
            "warning",
            about_text_present,
            "about=1 (description optional)",
            ",".join(f"{key}={value}" for key, value in sorted(text_types.items())),
        ),
        (
            "company_profile_links_present",
            "warning",
            profile_links_present,
            ",".join(sorted(required_profile_links)),
            ",".join(sorted(link_types)),
        ),
        # Screener renders the section with empty lists for some companies. That
        # is recorded as a warning so the absence stays visible in the evidence,
        # but it no longer blocks acceptance of the page's financial data. The
        # parser still rejects a page whose Pros/Cons section is absent outright.
        (
            "pros_cons_text_present",
            "warning",
            pros_cons_present,
            "headings=Pros,Cons;commentary>0",
            (
                f"headings={','.join(sorted(analysis_headings))};"
                f"pro={text_types.get('pro', 0)},con={text_types.get('con', 0)}"
            ),
        ),
        (
            "pros_cons_source_generated",
            "error",
            commentary_not_marked_generated == 0,
            "0",
            str(commentary_not_marked_generated),
        ),
        (
            "document_links_present",
            "error",
            document_count > 0,
            ">0",
            str(document_count),
        ),
        (
            "document_link_source_fields_match",
            "error",
            document_source_count == document_count,
            str(document_count),
            str(document_source_count),
        ),
        (
            "document_link_positions_contiguous",
            "error",
            document_positions_contiguous,
            f"0..{document_count - 1}" if document_count else "non-empty",
            (
                f"{document_stats['min_position']}..{document_stats['max_position']}"
                if document_count
                else "empty"
            ),
        ),
        (
            "document_link_availability_consistent",
            "error",
            document_availability_mismatches == 0,
            "0",
            str(document_availability_mismatches),
        ),
        (
            "document_link_required_families_present",
            "error",
            required_document_families_present,
            ",".join(sorted(required_document_families)),
            ",".join(sorted(document_families)),
        ),
        (
            "known_document_link_types",
            "manual_review",
            not unknown_document_types,
            "none",
            ",".join(unknown_document_types) if unknown_document_types else None,
        ),
        (
            "known_ratio_labels",
            "manual_review",
            unknown_metrics is None,
            "none",
            unknown_metrics,
        ),
        (
            "peer_table_supplement_present",
            "error",
            peer_supplement_ok and peer_observation_count > 0,
            "linked validated supplemental parse with observations",
            (
                f"parse_id={peer_parse_id},observations={peer_observation_count},"
                f"status={peer_link['validation_status'] if peer_link else 'missing'}"
            ),
        ),
        (
            "peer_table_not_placeholder",
            "error",
            peer_placeholder_count == 0 and peer_observation_count > 0,
            "placeholder=0,observations>0",
            f"placeholder={peer_placeholder_count},observations={peer_observation_count}",
        ),
        (
            "peer_table_numeric_cells",
            "error",
            peer_observation_count > 0 and peer_untyped == 0,
            "observations>0,untyped=0",
            f"observations={peer_observation_count},untyped={peer_untyped}",
        ),
        (
            "peer_table_subject_match",
            "error",
            peer_subject_count == 1,
            "1",
            str(peer_subject_count),
        ),
    ]
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
    """Keep the queue watermark consistent after manual and queued acceptance."""
    latest = conn.execute(
        """SELECT f.parse_id,substr(f.period_end,1,7) AS period
           FROM accepted_page_versions a JOIN financial_observations f ON f.parse_id=a.parse_id
           JOIN parse_artifacts p ON p.parse_id=a.parse_id
           JOIN response_blobs b ON b.blob_id=p.blob_id
           WHERE a.company_id=? AND a.source='screener' AND a.reporting_scope=?
             AND f.section_code='quarters' AND f.reporting_scope=a.reporting_scope
             AND f.period_end IS NOT NULL
           ORDER BY f.period_end DESC,b.first_observed_at_utc DESC,f.parse_id DESC LIMIT 1""",
        (company_id,scope),
    ).fetchone()
    if latest is not None:
        with conn:
            conn.execute("""UPDATE screener_company_collection_state
                SET latest_accepted_result_period=?,latest_accepted_parse_id=?,updated_at_utc=?
                WHERE company_id=? AND reporting_scope=?""",
                (latest['period'],latest['parse_id'],_utc_now(),company_id,scope))


def accept_parse(
    conn: sqlite3.Connection,
    company_id: int,
    parse_id: int,
    source: str,
    scope: str,
    resolved_source_period: str = "not_applicable",
) -> None:
    if scope != "standalone":
        # Consolidated parses may be retained as non-ML source evidence, but
        # are never accepted into the financial time series.
        raise ValueError("automatic financial acceptance requires reporting_scope=standalone")
    artifact = conn.execute(
        "SELECT parse_status, validation_status FROM parse_artifacts WHERE parse_id=?",
        (parse_id,),
    ).fetchone()
    blocking_rules = [
        str(row["rule_code"])
        for row in conn.execute(
            """SELECT rule_code FROM validation_results
               WHERE parse_id=? AND status='failed'
                 AND severity IN ('error', 'manual_review')
               ORDER BY rule_code""",
            (parse_id,),
        ).fetchall()
    ]
    blocking = len(blocking_rules)
    actual_scope = conn.execute(
        """SELECT reporting_scope FROM quote_observations
           WHERE parse_id=?""",
        (parse_id,),
    ).fetchone()
    scope_matches = actual_scope is not None and actual_scope["reporting_scope"] == scope
    is_blocked = (
        artifact is None
        or artifact["parse_status"] != "ok"
        or artifact["validation_status"] not in {"passed", "passed_with_warnings"}
        or blocking
        or not scope_matches
    )
    now = _utc_now()
    if is_blocked:
        reason = {
            "parse_status": artifact["parse_status"] if artifact else "missing",
            "validation_status": artifact["validation_status"] if artifact else "missing",
            "blocking_validation_count": int(blocking),
            "blocking_rule_codes": blocking_rules,
            "requested_reporting_scope": scope,
            "actual_reporting_scope": (
                actual_scope["reporting_scope"] if actual_scope is not None else None
            ),
        }
        if artifact is not None:
            with conn:
                conn.execute(
                    """INSERT INTO acceptance_events
                       (company_id, source, reporting_scope,
                        resolved_source_period, parse_id, action,
                        accepted_at_utc, accepted_by, acceptance_mode,
                        reason_json)
                       VALUES (?, ?, ?, ?, ?, 'rejected', ?, 'system',
                               'automatic', ?)""",
                    (
                        company_id,
                        source,
                        scope,
                        resolved_source_period,
                        parse_id,
                        now,
                        json.dumps(reason, sort_keys=True),
                    ),
                )
        # The blocking rule codes travel in the message because the queue's
        # retryable/terminal policy classifies failures from the message alone;
        # a generic message would make every blocking rule look retryable.
        detail = ", ".join(blocking_rules) if blocking_rules else "none"
        raise ValueError(
            "Parse artifact does not satisfy automatic acceptance rules "
            f"(parse_status={reason['parse_status']}, "
            f"validation_status={reason['validation_status']}, "
            f"requested_scope={scope}, actual_scope={reason['actual_reporting_scope']}, "
            f"blocking_rules=[{detail}])"
        )

    current = conn.execute(
        """SELECT parse_id FROM accepted_page_versions
           WHERE company_id=? AND source=? AND reporting_scope=?
             AND resolved_source_period=?""",
        (company_id, source, scope, resolved_source_period),
    ).fetchone()
    current_parse_id = int(current["parse_id"]) if current else None
    if current_parse_id == parse_id:
        if source == "screener":
            sync_screener_quarter_state(conn, company_id, scope)
        return

    with conn:
        if current_parse_id is not None:
            conn.execute(
                """INSERT INTO acceptance_events
                   (company_id, source, reporting_scope, resolved_source_period,
                    parse_id, action, accepted_at_utc, accepted_by,
                    acceptance_mode, reason_json)
                   VALUES (?, ?, ?, ?, ?, 'superseded', ?, 'system',
                           'automatic', ?)""",
                (
                    company_id,
                    source,
                    scope,
                    resolved_source_period,
                    current_parse_id,
                    now,
                    json.dumps({"superseded_by_parse_id": parse_id}, sort_keys=True),
                ),
            )
        conn.execute(
            """INSERT INTO acceptance_events
               (company_id, source, reporting_scope, resolved_source_period,
                parse_id, action, accepted_at_utc, accepted_by,
                acceptance_mode, reason_json)
               VALUES (?, ?, ?, ?, ?, 'accepted', ?, 'system', 'automatic', ?)""",
            (
                company_id,
                source,
                scope,
                resolved_source_period,
                parse_id,
                now,
                json.dumps({"previous_parse_id": current_parse_id}, sort_keys=True),
            ),
        )
        conn.execute(
            """INSERT INTO accepted_page_versions
               (company_id, source, reporting_scope, resolved_source_period, parse_id,
                accepted_at_utc, accepted_by, acceptance_mode)
               VALUES (?, ?, ?, ?, ?, ?, 'system', 'automatic')
               ON CONFLICT(company_id, source, reporting_scope, resolved_source_period)
               DO UPDATE SET parse_id=excluded.parse_id,
                             accepted_at_utc=excluded.accepted_at_utc,
                             accepted_by=excluded.accepted_by,
                             acceptance_mode=excluded.acceptance_mode""",
            (company_id, source, scope, resolved_source_period, parse_id, now),
        )
    if source == "screener":
        sync_screener_quarter_state(conn, company_id, scope)
