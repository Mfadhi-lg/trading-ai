"""Focused Trendlyne identity, month, and source-table validation."""

from __future__ import annotations

import json
import re
import sqlite3
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..core.nums import decimal_text, decimal_values, missing_kind as numeric_missing_kind
from ..sources.trendlyne.monthly_mf import fund_column_contract
from ..sources.trendlyne.urls import TrendlyneMapping


_RESOLVED_PERIOD = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _insert_rule(
    conn: sqlite3.Connection,
    parse_id: int,
    code: str,
    passed: bool,
    expected: object,
    actual: object,
    severity: str = "error",
) -> None:
    conn.execute(
        """INSERT INTO validation_results
           (parse_id, rule_code, severity, status, expected_value, actual_value,
            details_json, created_at_utc)
           VALUES (?, ?, ?, ?, ?, ?, '{}', ?)""",
        (
            parse_id,
            code,
            severity,
            "passed" if passed else "failed",
            json.dumps(expected, sort_keys=True),
            json.dumps(actual, sort_keys=True),
            _utc_now(),
        ),
    )


def validate_trendlyne_parse(
    conn: sqlite3.Connection,
    parse_id: int,
    mapping: TrendlyneMapping,
) -> str:
    page = conn.execute(
        "SELECT * FROM trendlyne_monthly_pages WHERE parse_id=?", (parse_id,)
    ).fetchone()
    if page is None:
        raise ValueError("Trendlyne page observation is missing")

    fund_section = conn.execute(
        """SELECT section_id FROM source_sections
           WHERE parse_id=? AND section_code='trendlyne_fund_table'""",
        (parse_id,),
    ).fetchone()
    fund_cells = []
    if fund_section is not None:
        fund_cells = list(
            conn.execute(
                """SELECT * FROM source_table_cells WHERE section_id=?
                   ORDER BY row_position, column_position""",
                (fund_section["section_id"],),
            )
        )
    fund_rows = list(
        conn.execute(
            """SELECT * FROM trendlyne_fund_source_rows WHERE parse_id=?
               ORDER BY row_position""",
            (parse_id,),
        )
    )
    fund_observations = list(
        conn.execute(
            """SELECT * FROM trendlyne_fund_observations WHERE parse_id=?
               ORDER BY table_position, row_position, column_position""",
            (parse_id,),
        )
    )
    summary = conn.execute(
        "SELECT * FROM trendlyne_mf_page_summaries WHERE parse_id=?", (parse_id,)
    ).fetchone()
    summary_section = conn.execute(
        """SELECT section_id FROM source_sections
           WHERE parse_id=? AND section_code='trendlyne_monthly_summary'""",
        (parse_id,),
    ).fetchone()
    summary_fields = []
    if summary_section is not None:
        summary_fields = list(
            conn.execute(
                """SELECT * FROM source_fields WHERE section_id=?
                   ORDER BY field_position""",
                (summary_section["section_id"],),
            )
        )

    month_section = conn.execute(
        """SELECT section_id FROM source_sections
           WHERE parse_id=? AND section_code='trendlyne_month_history'""",
        (parse_id,),
    ).fetchone()
    month_fields = []
    if month_section is not None:
        month_fields = list(
            conn.execute(
                """SELECT * FROM source_fields WHERE section_id=?
                   ORDER BY field_position""",
                (month_section["section_id"],),
            )
        )
    month_links = list(
        conn.execute(
            """SELECT * FROM trendlyne_month_links WHERE parse_id=?
               ORDER BY link_position""",
            (parse_id,),
        )
    )

    header_cells = [cell for cell in fund_cells if cell["cell_role"] == "column_header"]
    body_cells = [
        cell for cell in fund_cells if cell["cell_role"] in {"scheme", "data"}
    ]
    summary_cells = [
        cell
        for cell in fund_cells
        if cell["cell_role"] in {"summary_label", "summary_data"}
    ]
    body_by_row: dict[int, list[sqlite3.Row]] = {}
    for cell in body_cells:
        body_by_row.setdefault(int(cell["row_position"]), []).append(cell)
    logical_width = max(
        (int(cell["column_position"]) + int(cell["colspan"]) for cell in fund_cells),
        default=0,
    )
    body_shape_ok = bool(fund_rows) and all(
        len(body_by_row.get(int(row["row_position"]), [])) == logical_width
        and [
            int(cell["column_position"])
            for cell in body_by_row[int(row["row_position"])]
        ]
        == list(range(logical_width))
        for row in fund_rows
    )
    header_fingerprint = [
        (
            int(cell["row_position"]),
            int(cell["column_position"]),
            int(cell["rowspan"]),
            int(cell["colspan"]),
        )
        for cell in header_cells
    ]
    expected_header_fingerprint = [
        (0, 0, 2, 1),
        (0, 1, 1, 5),
        *[(0, column, 1, 2) for column in range(6, logical_width, 2)],
        *[(1, column, 1, 1) for column in range(1, logical_width)],
    ]
    summary_rows = {int(cell["row_position"]) for cell in summary_cells}
    summary_shape_ok = (
        len(summary_rows) == 1
        and len(summary_cells) == logical_width
        and [int(cell["column_position"]) for cell in summary_cells]
        == list(range(logical_width))
    )
    scheme_identity_ok = bool(fund_rows) and len(
        {row["source_scheme_id"] for row in fund_rows}
    ) == len(fund_rows) and all(
        re.fullmatch(r"\d+", row["source_scheme_id"] or "")
        and re.search(
            rf"/mutual-fund/nav/{re.escape(row['source_scheme_id'])}/[^/]+/$",
            row["source_url"] or "",
        )
        and body_by_row.get(int(row["row_position"]))
        and body_by_row[int(row["row_position"])][0]["normalized_display_text"]
        == row["scheme_name_raw"]
        and body_by_row[int(row["row_position"])][0]["link_url"]
        == row["source_url"]
        for row in fund_rows
    )

    expected_observation_columns = list(range(1, logical_width))
    observations_by_row: dict[int, list[sqlite3.Row]] = {}
    for observation in fund_observations:
        observations_by_row.setdefault(int(observation["row_position"]), []).append(
            observation
        )
    observations_complete = bool(fund_rows) and len(fund_observations) == len(
        fund_rows
    ) * len(expected_observation_columns) and all(
        [
            int(observation["column_position"])
            for observation in observations_by_row.get(int(row["row_position"]), [])
        ]
        == expected_observation_columns
        for row in fund_rows
    )
    source_rows_by_position = {
        int(row["row_position"]): row for row in fund_rows
    }
    source_cells_by_coordinate = {
        (int(cell["row_position"]), int(cell["column_position"])): cell
        for cell in body_cells
        if cell["cell_role"] == "data"
    }
    observations_match_source = observations_complete and all(
        int(observation["table_position"]) == 0
        and (source_row := source_rows_by_position.get(int(observation["row_position"])))
        is not None
        and observation["source_scheme_id"] == source_row["source_scheme_id"]
        and observation["scheme_name_raw"] == source_row["scheme_name_raw"]
        and (
            source_cell := source_cells_by_coordinate.get(
                (
                    int(observation["row_position"]),
                    int(observation["column_position"]),
                )
            )
        )
        is not None
        and observation["display_value"] == source_cell["normalized_display_text"]
        for observation in fund_observations
    )

    try:
        observation_contract = fund_column_contract(logical_width)
    except ValueError:
        observation_contract = {}

    def observation_period_matches(observation: sqlite3.Row) -> bool:
        cell = source_cells_by_coordinate.get(
            (int(observation["row_position"]), int(observation["column_position"]))
        )
        if cell is None:
            return False
        period_label = (cell["column_label_raw"] or "").split(" / ", 1)[0]
        try:
            parsed_period = datetime.strptime(period_label, "%b-%Y")
        except ValueError:
            return False
        period_end = parsed_period.replace(
            day=monthrange(parsed_period.year, parsed_period.month)[1]
        ).strftime("%Y-%m-%d")
        return (
            observation["period_label_raw"] == period_label
            and observation["source_period"] == parsed_period.strftime("%Y-%m")
            and observation["period_end"] == period_end
            and observation["period_type"] == "month"
        )

    observation_metadata_ok = observations_complete and all(
        (
            observation["metric_code"],
            observation["period_role"],
            observation["unit_code"],
            observation["scale_multiplier"],
        )
        == observation_contract[int(observation["column_position"])]
        and observation_period_matches(observation)
        for observation in fund_observations
    )

    def observation_numeric_matches(observation: sqlite3.Row) -> bool:
        missing_token = observation["display_value"]
        if observation["unit_code"] == "PERCENT_POINT" and missing_token.endswith(
            "%"
        ):
            missing_token = missing_token[:-1]
        expected_missing = numeric_missing_kind(missing_token)
        values = decimal_values(observation["display_value"])
        expected_value = (
            decimal_text(values[0])
            if expected_missing is None and len(values) == 1
            else None
        )
        if expected_missing is None and len(values) != 1:
            expected_missing = "parse_error"
        return (
            observation["value_decimal"] == expected_value
            and observation["missing_kind"] == expected_missing
        )

    observation_numeric_ok = observations_complete and all(
        observation_numeric_matches(observation)
        and observation["missing_kind"] != "parse_error"
        for observation in fund_observations
    )
    response = conn.execute(
        """SELECT rb.first_observed_at_utc
           FROM parse_artifacts AS pa
           JOIN response_blobs AS rb ON rb.blob_id=pa.blob_id
           WHERE pa.parse_id=?""",
        (parse_id,),
    ).fetchone()
    first_observed_at_utc = response["first_observed_at_utc"] if response else None
    observation_availability_ok = bool(first_observed_at_utc) and observations_complete and all(
        observation["first_observed_at_utc"] == first_observed_at_utc
        and observation["source_published_at_utc"] is None
        and observation["available_at_utc"] == first_observed_at_utc
        and observation["availability_basis"] == "first_observed"
        for observation in fund_observations
    )

    month_positions = [int(link["link_position"]) for link in month_links]
    month_periods = [link["source_period"] for link in month_links]
    month_fields_match = bool(month_links) and len(month_fields) == len(month_links) and all(
        int(field["field_position"]) == int(link["link_position"])
        and field["field_key_raw"] == "month_link"
        and field["normalized_display_text"] == link["period_label_raw"]
        and field["link_url"] == link["source_url"]
        and field["field_role"] == "period_link"
        for field, link in zip(month_fields, month_links)
    )
    month_identity_ok = bool(month_links) and all(
        link["source_company_id"] == mapping.source_company_id
        and link["source_symbol"] == mapping.source_symbol
        and link["source_slug"] == mapping.source_slug
        for link in month_links
    )
    month_periods_ok = bool(month_links)
    parsed_month_periods: list[datetime] = []
    try:
        for link in month_links:
            display_period = datetime.strptime(
                link["period_label_raw"], "%b %Y"
            )
            url_period = datetime.strptime(
                link["source_url_period_raw"], "%b-%Y"
            )
            if (
                display_period != url_period
                or link["source_period"] != display_period.strftime("%Y-%m")
            ):
                month_periods_ok = False
            parsed_month_periods.append(display_period)
    except (TypeError, ValueError):
        month_periods_ok = False
    month_periods_ok = bool(
        month_periods_ok
        and month_positions == list(range(len(month_links)))
        and len(set(month_periods)) == len(month_links)
        and len({link["source_url"] for link in month_links}) == len(month_links)
        and all(
            earlier > later
            for earlier, later in zip(parsed_month_periods, parsed_month_periods[1:])
        )
    )
    active_month_links = [link for link in month_links if int(link["is_active"]) == 1]
    month_active_ok = bool(
        len(active_month_links) == 1
        and active_month_links[0]["source_period"] == page["resolved_source_period"]
        and active_month_links[0]["period_label_raw"] == page["active_month_raw"]
        and active_month_links[0]["source_url"] == page["active_month_url"]
    )
    month_availability_ok = bool(first_observed_at_utc) and bool(month_links) and all(
        link["first_observed_at_utc"] == first_observed_at_utc
        and link["source_published_at_utc"] is None
        and link["available_at_utc"] == first_observed_at_utc
        and link["availability_basis"] == "first_observed"
        for link in month_links
    )

    top_headers = {
        int(cell["column_position"]): cell["normalized_display_text"]
        for cell in header_cells
        if int(cell["row_position"]) == 0
    }
    current_table_period = None
    comparison_table_periods: list[str] = []
    try:
        current_date = datetime.strptime(top_headers.get(1, ""), "%b-%Y")
        current_table_period = current_date.strftime("%Y-%m")
        comparison_table_periods = [
            datetime.strptime(top_headers[column], "%b-%Y").strftime("%Y-%m")
            for column in range(6, logical_width, 2)
        ]
    except ValueError:
        pass

    active_period = None
    if page["active_url_period_raw"] and re.fullmatch(
        r"[A-Z][a-z]{2}-\d{4}", page["active_url_period_raw"]
    ):
        active_period = datetime.strptime(
            page["active_url_period_raw"], "%b-%Y"
        ).strftime("%Y-%m")
    selected_period = page["resolved_source_period"]
    expected_comparison_periods: list[str] = []
    expected_current_period_end = None
    if selected_period and _RESOLVED_PERIOD.fullmatch(selected_period):
        selected_date = datetime.strptime(selected_period, "%Y-%m")
        expected_current_period_end = selected_date.replace(
            day=monthrange(selected_date.year, selected_date.month)[1]
        ).strftime("%Y-%m-%d")
        comparison_date = selected_date.replace(day=1)
        for _ in range(len(comparison_table_periods)):
            comparison_date = (comparison_date - timedelta(days=1)).replace(day=1)
            expected_comparison_periods.append(comparison_date.strftime("%Y-%m"))
    heading_period = None
    if page["heading_month_raw"]:
        try:
            heading_period = datetime.strptime(
                page["heading_month_raw"], "%b %Y"
            ).strftime("%Y-%m")
        except ValueError:
            pass

    current_changes = [
        observation
        for observation in fund_observations
        if observation["metric_code"] == "month_change"
        and observation["period_role"] == "current"
    ]
    current_shares = [
        observation
        for observation in fund_observations
        if observation["metric_code"] == "shares_held"
        and observation["period_role"] == "current"
    ]
    recomputed: dict[str, str] | None = None
    recomputed_total_shares: str | None = None
    try:
        numeric_changes = [
            Decimal(observation["value_decimal"])
            for observation in current_changes
            if observation["value_decimal"] is not None
        ]
        recomputed = {
            "computed_buyers": str(sum(value > 0 for value in numeric_changes)),
            "computed_sellers": str(sum(value < 0 for value in numeric_changes)),
            "computed_holders": str(
                sum(
                    observation["value_decimal"] is not None
                    for observation in current_shares
                )
            ),
            "computed_net_change": decimal_text(
                sum(numeric_changes, Decimal("0"))
            ),
        }
        recomputed_total_shares = decimal_text(
            sum(
                (
                    Decimal(observation["value_decimal"])
                    for observation in current_shares
                    if observation["value_decimal"] is not None
                ),
                Decimal("0"),
            )
        )
    except (InvalidOperation, TypeError):
        pass

    footer_by_column = {
        int(cell["column_position"]): cell
        for cell in summary_cells
        if cell["cell_role"] == "summary_data"
    }
    source_summary_ok = bool(
        summary
        and len(summary_fields) == 1
        and summary_fields[0]["field_key_raw"] == "headline_summary"
        and summary_fields[0]["field_role"] == "untrusted_source_summary"
        and summary_fields[0]["raw_text"] == summary["headline_raw"]
        and summary_fields[0]["normalized_display_text"]
        == summary["headline_display_text"]
        and footer_by_column.get(3)
        and footer_by_column[3]["normalized_display_text"]
        == summary["table_total_shares_display"]
        and footer_by_column.get(4)
        and footer_by_column[4]["normalized_display_text"]
        == summary["table_total_change_display"]
        and summary["headline_buyers"]
        == decimal_text(decimal_values(summary["headline_buyers_display"])[0])
        and summary["headline_sellers"]
        == decimal_text(decimal_values(summary["headline_sellers_display"])[0])
        and summary["headline_net_change"]
        == decimal_text(decimal_values(summary["headline_net_change_display"])[0])
        and summary["table_total_shares"]
        == decimal_text(decimal_values(summary["table_total_shares_display"])[0])
        and summary["table_total_change"]
        == decimal_text(decimal_values(summary["table_total_change_display"])[0])
    )
    summary_period_ok = bool(
        summary
        and summary["source_period"] == selected_period
        and summary["headline_period_label_raw"] == page["heading_month_raw"]
        and summary["period_label_raw"] == top_headers.get(1)
        and summary["period_end"] == expected_current_period_end
        and summary["first_observed_at_utc"] == first_observed_at_utc
        and summary["source_published_at_utc"] is None
        and summary["available_at_utc"] == first_observed_at_utc
        and summary["availability_basis"] == "first_observed"
    )
    computed_reproducible = bool(
        summary
        and recomputed
        and all(summary[key] == value for key, value in recomputed.items())
    )
    table_matches_computed = bool(
        summary
        and recomputed
        and summary["table_total_change"] == recomputed["computed_net_change"]
    )
    table_shares_match_fund_rows = bool(
        summary
        and recomputed_total_shares is not None
        and summary["table_total_shares"] == recomputed_total_shares
    )
    headline_counts_match = bool(
        summary
        and recomputed
        and summary["headline_buyers"] == recomputed["computed_buyers"]
        and summary["headline_sellers"] == recomputed["computed_sellers"]
    )
    headline_net_matches = bool(
        summary
        and recomputed
        and summary["headline_net_change"] == recomputed["computed_net_change"]
    )
    canonicality_ok = bool(
        summary
        and int(summary["headline_is_canonical"]) == 0
        and int(summary["computed_values_are_canonical"]) == 1
    )

    rules = (
        (
            "trendlyne_identity_company_id",
            page["source_company_id"] == mapping.source_company_id
            and page["canonical_source_company_id"] == mapping.source_company_id,
            mapping.source_company_id,
            {
                "page": page["source_company_id"],
                "canonical": page["canonical_source_company_id"],
            },
        ),
        (
            "trendlyne_identity_symbol",
            page["source_symbol"] == mapping.source_symbol
            and page["canonical_symbol"] == mapping.source_symbol,
            mapping.source_symbol,
            {
                "page": page["source_symbol"],
                "canonical": page["canonical_symbol"],
            },
        ),
        (
            "trendlyne_identity_slug",
            page["source_slug"] == mapping.source_slug,
            mapping.source_slug,
            page["source_slug"],
        ),
        (
            "trendlyne_canonical_url",
            (
                page["canonical_url"] == mapping.canonical_url
                and page["canonical_period"] == "latest"
            )
            or (
                page["canonical_url"] == page["active_month_url"]
                and page["canonical_period"] == page["active_url_period_raw"]
            ),
            "latest canonical URL or the selected historical month URL",
            {
                "canonical_url": page["canonical_url"],
                "canonical_period": page["canonical_period"],
            },
        ),
        (
            "trendlyne_selected_month_sources_match",
            bool(selected_period)
            and selected_period == active_period == heading_period
            and page["selected_month_raw"] == page["active_month_raw"]
            == page["heading_month_raw"],
            "selected button = active link = heading month",
            {
                "selected": page["selected_month_raw"],
                "active": page["active_month_raw"],
                "heading": page["heading_month_raw"],
                "active_url_period": page["active_url_period_raw"],
            },
        ),
        (
            "trendlyne_resolved_month_canonical",
            bool(selected_period)
            and bool(_RESOLVED_PERIOD.fullmatch(selected_period))
            and selected_period.lower() != "latest",
            "YYYY-MM and never latest",
            selected_period,
        ),
        (
            "trendlyne_month_history_present",
            month_section is not None and bool(month_links),
            "one non-empty ordered month-link section",
            {
                "section_count": 1 if month_section is not None else 0,
                "month_links": len(month_links),
            },
        ),
        (
            "trendlyne_month_history_source_fields_match",
            month_fields_match,
            "every typed month link matches one ordered source field",
            {"matched": month_fields_match},
        ),
        (
            "trendlyne_month_history_identity",
            month_identity_ok,
            {
                "source_company_id": mapping.source_company_id,
                "source_symbol": mapping.source_symbol,
                "source_slug": mapping.source_slug,
            },
            {"matched": month_identity_ok},
        ),
        (
            "trendlyne_month_history_periods",
            month_periods_ok,
            "unique contiguous links in descending month order with matching labels and URLs",
            {
                "month_links": len(month_links),
                "positions": month_positions,
                "periods": month_periods,
            },
        ),
        (
            "trendlyne_month_history_active_selected",
            month_active_ok,
            page["resolved_source_period"],
            [link["source_period"] for link in active_month_links],
        ),
        (
            "trendlyne_month_history_availability",
            month_availability_ok,
            {
                "available_at": "response first_observed_at_utc",
                "availability_basis": "first_observed",
            },
            {"matched": month_availability_ok},
        ),
        (
            "trendlyne_fund_table_present",
            fund_section is not None and bool(fund_rows) and bool(fund_cells),
            "one non-empty source-faithful fund table",
            {
                "section_count": 1 if fund_section is not None else 0,
                "fund_rows": len(fund_rows),
                "source_cells": len(fund_cells),
            },
        ),
        (
            "trendlyne_fund_table_known_structure",
            logical_width >= 8
            and (logical_width - 6) % 2 == 0
            and header_fingerprint == expected_header_fingerprint
            and body_shape_ok
            and summary_shape_ok,
            {
                "logical_columns": "8 + two per additional comparison month",
                "header_fingerprint": expected_header_fingerprint,
                "body_cells_per_row": logical_width,
                "footer_cells": logical_width,
            },
            {
                "logical_columns": logical_width,
                "header_fingerprint": header_fingerprint,
                "body_shape_ok": body_shape_ok,
                "summary_shape_ok": summary_shape_ok,
            },
        ),
        (
            "trendlyne_fund_source_cells_complete",
            len(header_cells) == len(expected_header_fingerprint)
            and len(body_cells) == len(fund_rows) * logical_width
            and len(summary_cells) == logical_width,
            {
                "header_cells": len(expected_header_fingerprint),
                "body_cells": f"fund_rows * {logical_width}",
                "summary_cells": logical_width,
            },
            {
                "header_cells": len(header_cells),
                "body_cells": len(body_cells),
                "fund_rows": len(fund_rows),
                "summary_cells": len(summary_cells),
            },
        ),
        (
            "trendlyne_fund_scheme_identity",
            scheme_identity_ok,
            "unique numeric scheme ID matching each scheme URL and source cell",
            {"scheme_rows": len(fund_rows), "identity_match": scheme_identity_ok},
        ),
        (
            "trendlyne_fund_header_months_match",
            bool(selected_period)
            and current_table_period == selected_period
            and comparison_table_periods == expected_comparison_periods,
            {
                "current": selected_period,
                "comparison": expected_comparison_periods,
            },
            {
                "current_raw": top_headers.get(1),
                "current": current_table_period,
                "comparison_raw": [
                    top_headers.get(column)
                    for column in range(6, logical_width, 2)
                ],
                "comparison": comparison_table_periods,
            },
        ),
        (
            "trendlyne_fund_observations_complete",
            observations_complete,
            {
                "observations_per_fund": logical_width - 1,
                "columns": expected_observation_columns,
                "retain_all_source_funds": True,
            },
            {
                "source_funds": len(fund_rows),
                "observation_funds": len(observations_by_row),
                "observations": len(fund_observations),
            },
        ),
        (
            "trendlyne_fund_observations_match_source_cells",
            observations_match_source,
            "scheme identity and display value match every captured source cell",
            {"matched": observations_match_source},
        ),
        (
            "trendlyne_fund_observation_metadata",
            observation_metadata_ok,
            observation_contract,
            {"matched": observation_metadata_ok},
        ),
        (
            "trendlyne_fund_observation_numeric_values",
            observation_numeric_ok,
            "one exact decimal or an explicit source missing kind per cell",
            {"matched": observation_numeric_ok},
        ),
        (
            "trendlyne_fund_observation_availability",
            observation_availability_ok,
            {
                "available_at": "response first_observed_at_utc",
                "availability_basis": "first_observed",
            },
            {"matched": observation_availability_ok},
        ),
    )
    summary_rules = (
        (
            "trendlyne_monthly_summary_sources_preserved",
            "error",
            source_summary_ok,
            "headline source field and footer totals remain independent source evidence",
            {"matched": source_summary_ok},
        ),
        (
            "trendlyne_monthly_summary_metadata",
            "error",
            summary_period_ok,
            {
                "source_period": selected_period,
                "availability_basis": "first_observed",
            },
            {"matched": summary_period_ok},
        ),
        (
            "trendlyne_computed_activity_reproducible",
            "error",
            computed_reproducible,
            recomputed,
            dict(summary) if summary else None,
        ),
        (
            "trendlyne_table_total_matches_computed_net_change",
            "error",
            table_matches_computed,
            recomputed["computed_net_change"] if recomputed else None,
            summary["table_total_change"] if summary else None,
        ),
        (
            "trendlyne_table_total_shares_match_fund_rows",
            "error",
            table_shares_match_fund_rows,
            recomputed_total_shares,
            summary["table_total_shares"] if summary else None,
        ),
        (
            "trendlyne_headline_counts_match_computed",
            "warning",
            headline_counts_match,
            (
                {
                    "buyers": recomputed["computed_buyers"],
                    "sellers": recomputed["computed_sellers"],
                }
                if recomputed
                else None
            ),
            (
                {
                    "buyers": summary["headline_buyers"],
                    "sellers": summary["headline_sellers"],
                }
                if summary
                else None
            ),
        ),
        (
            "trendlyne_headline_net_change_matches_computed",
            "warning",
            headline_net_matches,
            recomputed["computed_net_change"] if recomputed else None,
            summary["headline_net_change"] if summary else None,
        ),
        (
            "trendlyne_headline_never_canonical",
            "error",
            canonicality_ok,
            {"headline_is_canonical": 0, "computed_values_are_canonical": 1},
            (
                {
                    "headline_is_canonical": summary["headline_is_canonical"],
                    "computed_values_are_canonical": summary[
                        "computed_values_are_canonical"
                    ],
                }
                if summary
                else None
            ),
        ),
    )
    with conn:
        for code, passed, expected, actual in rules:
            _insert_rule(conn, parse_id, code, bool(passed), expected, actual)
        for code, severity, passed, expected, actual in summary_rules:
            _insert_rule(
                conn,
                parse_id,
                code,
                bool(passed),
                expected,
                actual,
                severity,
            )
        hard_fail = any(not passed for _, passed, _, _ in rules) or any(
            not passed and severity in {"error", "manual_review"}
            for _, severity, passed, _, _ in summary_rules
        )
        warning = any(
            not passed and severity == "warning"
            for _, severity, passed, _, _ in summary_rules
        )
        status = (
            "failed"
            if hard_fail
            else "passed_with_warnings"
            if warning
            else "passed"
        )
        conn.execute(
            "UPDATE parse_artifacts SET validation_status=? WHERE parse_id=?",
            (status, parse_id),
        )
    return status


def accept_trendlyne_parse(
    conn: sqlite3.Connection,
    company_id: int,
    parse_id: int,
    mapping: TrendlyneMapping,
) -> str:
    artifact = conn.execute(
        "SELECT parse_status, validation_status FROM parse_artifacts WHERE parse_id=?",
        (parse_id,),
    ).fetchone()
    page = conn.execute(
        "SELECT * FROM trendlyne_monthly_pages WHERE parse_id=?", (parse_id,)
    ).fetchone()
    blocking = conn.execute(
        """SELECT COUNT(*) AS n FROM validation_results
           WHERE parse_id=? AND status='failed'
             AND severity IN ('error', 'manual_review')""",
        (parse_id,),
    ).fetchone()["n"]
    resolved = page["resolved_source_period"] if page else None
    intrinsic_match = bool(
        page
        and page["reporting_scope"] == "not_applicable"
        and page["source_company_id"] == mapping.source_company_id
        and page["source_symbol"] == mapping.source_symbol
        and page["source_slug"] == mapping.source_slug
        and resolved
        and _RESOLVED_PERIOD.fullmatch(resolved)
        and resolved != "latest"
    )
    blocked = bool(
        artifact is None
        or artifact["parse_status"] != "ok"
        or artifact["validation_status"] not in {"passed", "passed_with_warnings"}
        or blocking
        or not intrinsic_match
    )
    now = _utc_now()
    if blocked:
        if artifact is not None:
            with conn:
                conn.execute(
                    """INSERT INTO acceptance_events
                       (company_id, source, reporting_scope,
                        resolved_source_period, parse_id, action,
                        accepted_at_utc, accepted_by, acceptance_mode, reason_json)
                       VALUES (?, 'trendlyne', 'not_applicable', ?, ?, 'rejected',
                               ?, 'system', 'automatic', ?)""",
                    (
                        company_id,
                        resolved or "unknown",
                        parse_id,
                        now,
                        json.dumps(
                            {
                                "blocking_validation_count": int(blocking),
                                "intrinsic_identity_and_period_match": intrinsic_match,
                            },
                            sort_keys=True,
                        ),
                    ),
                )
        raise ValueError("Trendlyne parse does not satisfy automatic acceptance rules")

    current = conn.execute(
        """SELECT parse_id FROM accepted_page_versions
           WHERE company_id=? AND source='trendlyne'
             AND reporting_scope='not_applicable' AND resolved_source_period=?""",
        (company_id, resolved),
    ).fetchone()
    current_parse_id = int(current["parse_id"]) if current else None
    if current_parse_id == parse_id:
        return str(resolved)
    with conn:
        if current_parse_id is not None:
            conn.execute(
                """INSERT INTO acceptance_events
                   (company_id, source, reporting_scope, resolved_source_period,
                    parse_id, action, accepted_at_utc, accepted_by,
                    acceptance_mode, reason_json)
                   VALUES (?, 'trendlyne', 'not_applicable', ?, ?, 'superseded',
                           ?, 'system', 'automatic', ?)""",
                (
                    company_id,
                    resolved,
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
               VALUES (?, 'trendlyne', 'not_applicable', ?, ?, 'accepted', ?,
                       'system', 'automatic', ?)""",
            (
                company_id,
                resolved,
                parse_id,
                now,
                json.dumps({"previous_parse_id": current_parse_id}, sort_keys=True),
            ),
        )
        conn.execute(
            """INSERT INTO accepted_page_versions
               (company_id, source, reporting_scope, resolved_source_period,
                parse_id, accepted_at_utc, accepted_by, acceptance_mode)
               VALUES (?, 'trendlyne', 'not_applicable', ?, ?, ?, 'system',
                       'automatic')
               ON CONFLICT(company_id, source, reporting_scope,
                           resolved_source_period)
               DO UPDATE SET parse_id=excluded.parse_id,
                             accepted_at_utc=excluded.accepted_at_utc,
                             accepted_by=excluded.accepted_by,
                             acceptance_mode=excluded.acceptance_mode""",
            (company_id, resolved, parse_id, now),
        )
    return str(resolved)
