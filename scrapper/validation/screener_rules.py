"""Pure Screener validation rule groups.

Observation reads, timestamps, result persistence and acceptance remain in the
compatible screener facade. Groups return ordered legacy rule tuples.
"""
from __future__ import annotations


def peer_fragment_rules(
    *,
    company_count,
    expected_source_company_id,
    expected_symbol,
    numeric_failures,
    observation_count,
    placeholder_count,
    source_cell_count,
    subject,
    subject_ok,
    unknown_metrics,
):
    return [
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


def identity_rules(
    *,
    actual_identifiers,
    expected_scope,
    expected_symbol,
    manifest_errors,
    scope,
):
    return [
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
    ]


def financial_rules(
    *,
    balance_count,
    balance_imbalance_count,
    balance_imbalance_detail,
    cash_count,
    financial_count,
    metric_count,
    numeric_failures,
    pnl_count,
    pnl_period_collisions,
    ratios_count,
    ratios_panel_collisions,
    ratios_untyped,
):
    return [
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
    ]


def growth_rules(
    *,
    growth_count,
    growth_missing_as_value,
    growth_untyped,
):
    return [
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
    ]


def shareholding_rules(
    *,
    shareholding_bad_regime,
    shareholding_count,
    shareholding_total_anomalies,
    shareholding_total_detail,
    shareholding_untyped,
    shareholding_warning_captured,
    shareholding_warning_present,
    source_has_shareholding_warning,
    unavailable_frequencies,
    yearly_bad_regime,
    yearly_count,
    yearly_total_anomalies,
    yearly_total_detail,
    yearly_untyped,
):
    return [
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
    ]


def profile_rules(
    *,
    about_text_present,
    analysis_headings,
    commentary_not_marked_generated,
    link_types,
    profile_links_present,
    pros_cons_present,
    required_profile_links,
    text_types,
):
    return [
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
    ]


def document_rules(
    *,
    document_availability_mismatches,
    document_count,
    document_families,
    document_positions_contiguous,
    document_source_count,
    document_stats,
    required_document_families,
    required_document_families_present,
    unknown_document_types,
):
    return [
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
    ]


def metric_review_rules(
    *,
    unknown_metrics,
):
    return [
        (
            "known_ratio_labels",
            "manual_review",
            unknown_metrics is None,
            "none",
            unknown_metrics,
        ),
    ]


def peer_supplement_rules(
    *,
    peer_link,
    peer_observation_count,
    peer_parse_id,
    peer_placeholder_count,
    peer_subject_count,
    peer_supplement_ok,
    peer_untyped,
):
    return [
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
