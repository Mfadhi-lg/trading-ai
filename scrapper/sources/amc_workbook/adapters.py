"""Publisher adapter registry for official AMC workbook formats."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ppfas import NormalizedHolding, PPFASWorkbook, PPFASStructureError


@dataclass(frozen=True)
class WorkbookAdapter:
    publisher: str
    parse: Callable[..., PPFASWorkbook]
    normalize: Callable[[PPFASWorkbook], tuple[NormalizedHolding, ...]]
    load_manifest: Callable[[], dict]
    expected_scheme_codes: Callable[[dict, str], tuple[str, ...]]
    allow_ooxml_xls_extension: bool = False
    allow_legacy_xls_parse: bool = False

    @property
    def structure_validation_rules(self) -> tuple[str, ...]:
        return tuple(
            f"{self.publisher}_{suffix}"
            for suffix in (
                "publisher_matches_cli",
                "disclosure_month_matches_cli",
                "sheet_inventory_matches_manifest",
                "all_sheets_classified",
                "required_columns_complete",
                "table_boundaries_complete",
                "source_cells_complete",
                "merged_ranges_captured",
                "formulas_preserved",
            )
        )


def get_workbook_adapter(publisher: str) -> WorkbookAdapter | None:
    """Resolve only explicitly implemented publisher formats."""
    if publisher == "ppfas":
        from .ppfas import (
            expected_scheme_codes,
            load_manifest,
            normalize_ppfas_holdings,
            parse_ppfas_workbook,
        )

        return WorkbookAdapter(
            publisher,
            parse_ppfas_workbook,
            normalize_ppfas_holdings,
            load_manifest,
            expected_scheme_codes,
            allow_ooxml_xls_extension=True,
            allow_legacy_xls_parse=True,
        )
    if publisher == "groww":
        from .groww import (
            expected_scheme_codes,
            load_manifest,
            normalize_groww_holdings,
            parse_groww_workbook,
        )

        return WorkbookAdapter(
            publisher,
            parse_groww_workbook,
            normalize_groww_holdings,
            load_manifest,
            expected_scheme_codes,
        )
    if publisher == "motilal_oswal":
        from .motilal_oswal import (
            expected_scheme_codes,
            load_manifest,
            normalize_motilal_oswal_holdings,
            parse_motilal_oswal_workbook,
        )

        return WorkbookAdapter(
            publisher,
            parse_motilal_oswal_workbook,
            normalize_motilal_oswal_holdings,
            load_manifest,
            expected_scheme_codes,
        )
    return None


def adapter_for_parsed(parsed: PPFASWorkbook) -> WorkbookAdapter:
    adapter = get_workbook_adapter(parsed.detected_publisher)
    if adapter is None:
        raise PPFASStructureError(
            "unsupported_publisher",
            f"No workbook adapter for publisher {parsed.detected_publisher!r}",
        )
    return adapter


__all__ = ["WorkbookAdapter", "adapter_for_parsed", "get_workbook_adapter"]
