"""NSE symbol-change and company-name-change files.

These files are the only evidence that keeps a renamed security the same durable
security. Names alone never merge or split a company; a change is applied only
when it resolves to an exchange key already present in the registry.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.nums import clean_text
from .security_master import _NSE_SYMBOL_RE
from .tabular import (
    RejectedRow,
    SourceRowError,
    parse_source_date,
    read_delimited,
    read_fixed_columns,
)


NSE_SYMBOL_CHANGE = "nse_symbol_change"
NSE_NAME_CHANGE = "nse_name_change"
CORPORATE_CHANGE_DATASETS = (NSE_SYMBOL_CHANGE, NSE_NAME_CHANGE)

# The published symbol-change file is headerless; this is its declared contract.
_SYMBOL_CHANGE_COLUMNS = ("company_name", "old_symbol", "new_symbol", "effective_date")
_SYMBOL_CHANGE_HEADER_HINTS = ("old symbol", "new symbol")
_NAME_CHANGE_REQUIRED = {
    "symbol": ("SYMBOL", "NCH_SYMBOL"),
    "old_name": ("OLD NAME", "OLD COMPANY NAME", "PREVIOUS NAME", "NCH_PREV_NAME"),
    "new_name": ("NEW NAME", "NEW COMPANY NAME", "CURRENT NAME", "NCH_NEW_NAME"),
    "effective_date": (
        "DATE OF CHANGE",
        "EFFECTIVE DATE",
        "DATE OF LISTING",
        "NCH_DT",
    ),
}


@dataclass(frozen=True)
class CorporateChange:
    row_position: int
    exchange: str
    change_kind: str
    company_name_raw: str
    old_value: str
    new_value: str
    effective_date: str
    key_hints: tuple[str, ...]
    raw_cells: tuple[str, ...]


@dataclass(frozen=True)
class ParsedCorporateChanges:
    dataset: str
    exchange: str
    headers: tuple[str, ...]
    encoding: str
    changes: tuple[CorporateChange, ...]
    rejected: tuple[RejectedRow, ...]

    @property
    def accepted_row_count(self) -> int:
        return len(self.changes)

    @property
    def rejected_row_count(self) -> int:
        return len(self.rejected)


def _symbol(value: str, *, field_name: str) -> str:
    text = clean_text(value).upper()
    if not _NSE_SYMBOL_RE.fullmatch(text):
        raise SourceRowError("invalid_symbol", f"{field_name} is not well formed: {value!r}")
    return text


def parse_corporate_changes(content: bytes, dataset: str) -> ParsedCorporateChanges:
    """Parse one dated NSE change file offline and fail closed per source row."""
    if dataset not in CORPORATE_CHANGE_DATASETS:
        raise ValueError(f"Unknown corporate-change dataset: {dataset!r}")
    if dataset == NSE_SYMBOL_CHANGE:
        table = read_fixed_columns(
            content,
            columns=_SYMBOL_CHANGE_COLUMNS,
            header_hints=_SYMBOL_CHANGE_HEADER_HINTS,
        )
    else:
        table = read_delimited(content, required=_NAME_CHANGE_REQUIRED)

    changes: list[CorporateChange] = []
    rejected: list[RejectedRow] = []
    for row in table.rows:
        try:
            effective = parse_source_date(row.value("effective_date"), field="effective date")
            if dataset == NSE_SYMBOL_CHANGE:
                old_value = _symbol(row.value("old_symbol"), field_name="old symbol")
                new_value = _symbol(row.value("new_symbol"), field_name="new symbol")
                name = clean_text(row.value("company_name"))
                hints = (new_value, old_value)
                kind = "symbol"
            else:
                symbol = _symbol(row.value("symbol"), field_name="symbol")
                old_value = clean_text(row.value("old_name"))
                new_value = clean_text(row.value("new_name"))
                name = new_value
                hints = (symbol,)
                kind = "name"
            if not old_value or not new_value:
                raise SourceRowError("missing_value", "Change row has a blank old or new value")
            if old_value == new_value:
                raise SourceRowError("no_change", "Change row repeats the same value")
            if not name:
                raise SourceRowError("missing_name", "Change row has no company name")
        except SourceRowError as exc:
            rejected.append(RejectedRow(row.row_position, exc.reason_code, exc.detail, row.cells))
            continue
        changes.append(
            CorporateChange(
                row.row_position, "NSE", kind, name, old_value, new_value,
                effective, hints, row.cells,
            )
        )
    return ParsedCorporateChanges(
        dataset, "NSE", table.headers, table.encoding, tuple(changes), tuple(rejected)
    )
