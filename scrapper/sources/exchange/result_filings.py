"""Official NSE/BSE financial-result filing feeds.

A filing carries the reporting period, the consolidated/standalone scope and the
broadcast instant. Those three facts are what make result discovery incremental:
only filings broadcast after the stored watermark are new, and only a period
that advances beyond accepted state creates collection work.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from ...core.nums import clean_text
from .security_master import _BSE_CODE_RE, _NSE_SYMBOL_RE
from .tabular import (
    RejectedRow,
    SourceRow,
    SourceRowError,
    normalize_isin,
    parse_ist_timestamp,
    parse_source_date,
    is_json_payload,
    period_from_date,
    read_delimited,
    read_json_records,
)


NSE_FINANCIAL_RESULTS = "nse_financial_results"
BSE_FINANCIAL_RESULTS = "bse_financial_results"
RESULT_FILING_DATASETS = (NSE_FINANCIAL_RESULTS, BSE_FINANCIAL_RESULTS)

_CONSOLIDATED = {"consolidated", "c", "con"}
_STANDALONE = {
    "standalone", "non-consolidated", "non consolidated", "nonconsolidated",
    "non-consolidated (standalone)", "s", "non con", "unconsolidated",
}

# The camelCase aliases are the published NSE JSON API keys; the spaced aliases
# are the same logical columns as published in the delimited exports.
_NSE_REQUIRED = {
    "symbol": ("SYMBOL",),
    "company_name": ("COMPANY NAME", "NAME OF COMPANY", "companyName"),
    "period_end": (
        "PERIOD ENDED",
        "PERIOD END",
        "TO DATE",
        "QUARTER ENDED",
        "QUARTER END DATE",
        "toDate",
        "qe_Date",
    ),
    "scope": (
        "CONSOLIDATED",
        "CONSOLIDATED / NON-CONSOLIDATED (STANDALONE)",
        "CONSOLIDATED/NON-CONSOLIDATED",
        "RESULT TYPE",
        "TYPE OF RESULT",
        "CONSOLIDATED / STANDALONE",
    ),
    "broadcast": (
        "BROADCAST DATE/TIME",
        "BROADCAST DATE TIME",
        "BROADCAST DATE",
        "DISSEMINATION DATE/TIME",
        "broadCastDate",
        "broadcast_Date",
    ),
}
_NSE_OPTIONAL = {
    "isin": ("ISIN", "ISIN NUMBER", "ISIN CODE"),
    # "PERIOD" is the periodicity ("Quarterly"), a different fact that both feeds
    # publish alongside "RELATING TO"; binding it here would be ambiguous.
    "relating_to": ("RELATING TO", "relatingTo"),
    "audited": ("AUDITED", "AUDITED / UNAUDITED"),
    "cumulative": ("CUMULATIVE",),
    "industry": ("INDUSTRY",),
    # Integrated Filing revisions leave the original broadcast column blank
    # and publish their effective filing instant in this dedicated column.
    "revised_broadcast": ("REVISED DATE/TIME", "revised_Date"),
}
_BSE_REQUIRED = {
    "scrip_code": ("SECURITY CODE", "SCRIP CODE", "SCRIPCODE", "SC_CODE"),
    "company_name": ("SECURITY NAME", "COMPANY NAME", "SHORT NAME", "SC_NAME"),
    "period_end": ("PERIOD ENDED", "PERIOD END", "QUARTER ENDED", "TO DATE"),
    "scope": ("CONSOLIDATED", "RESULT TYPE", "TYPE", "REPORTING SCOPE"),
    "broadcast": (
        "SUBMISSION DATE",
        "DISSEMINATION DATE/TIME",
        "NEWS SUBMISSION DATE",
        "BROADCAST DATE/TIME",
        "BROADCAST DATE",
    ),
}
_BSE_CURRENT_EXPORT_REQUIRED = {
    "scrip_code": ("SECURITY CODE",),
    "company_name": ("SECURITY NAME",),
    "period_end": ("PERIOD",),
    "broadcast": ("BROADCAST DATE & TIME", "BROADCAST DATE/TIME", "BROADCAST DATE"),
}
_BSE_OPTIONAL = {
    "isin": ("ISIN NO", "ISIN", "ISIN NUMBER"),
    "security_id": ("SECURITY ID", "SCRIP ID"),
    "relating_to": ("RELATING TO", "RESULT FOR", "relatingTo"),
    "audited": ("AUDITED",),
}
_BSE_INTEGRATED_REQUIRED = {
    "scrip_code": ("Scrip_cd",),
    "company_name": ("company_name",),
    "quarter_code": ("quarter_code",),
    "broadcast": ("Fld_CreateDate",),
}
_BSE_INTEGRATED_OPTIONAL = {
    "audited": ("audited",),
    "standalone_content": ("XMLName",),
    "consolidated_content": ("Consol_XMLName",),
}
_BSE_QUARTER_CODE_RE = re.compile(r"^([JSDM])Q(\d{4})-(\d{4})$")


@dataclass(frozen=True)
class ResultFiling:
    row_position: int
    exchange: str
    exchange_key: str
    isin: str | None
    company_name_raw: str
    result_period: str
    period_end_date: str
    reporting_scope: str
    broadcast_raw: str
    broadcast_at_utc: str
    relating_to_raw: str | None
    audited_raw: str | None
    cumulative_raw: str | None
    raw_row: Mapping[str, str]
    raw_cells: tuple[str, ...]

    @property
    def filing_key(self) -> str:
        return "|".join(
            (
                self.exchange_key,
                self.period_end_date,
                self.reporting_scope,
                self.broadcast_at_utc,
            )
        )

    @property
    def raw_row_json(self) -> str:
        return json.dumps(dict(sorted(self.raw_row.items())), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ParsedResultFilings:
    dataset: str
    exchange: str
    headers: tuple[str, ...]
    encoding: str
    filings: tuple[ResultFiling, ...]
    rejected: tuple[RejectedRow, ...]
    excluded_row_count: int = 0

    @property
    def accepted_row_count(self) -> int:
        return len(self.filings)

    @property
    def rejected_row_count(self) -> int:
        return len(self.rejected)

    @property
    def max_broadcast_at_utc(self) -> str | None:
        return max((item.broadcast_at_utc for item in self.filings), default=None)


def normalize_reporting_scope(value: str) -> str:
    text = clean_text(value).lower()
    if text in _CONSOLIDATED:
        return "consolidated"
    if text in _STANDALONE:
        return "standalone"
    raise SourceRowError("unknown_scope", f"Reporting scope is unrecognised: {value!r}")


def _raw_row(row: SourceRow, headers: tuple[str, ...]) -> dict[str, str]:
    return {clean_text(header) or f"column_{index}": row.cells[index]
            for index, header in enumerate(headers)}


def _filing(row: SourceRow, *, exchange: str, headers: tuple[str, ...]) -> ResultFiling:
    if exchange == "NSE":
        key = clean_text(row.value("symbol")).upper()
        if not _NSE_SYMBOL_RE.fullmatch(key):
            raise SourceRowError("invalid_symbol", f"NSE symbol is not well formed: {key!r}")
    else:
        key = clean_text(row.value("scrip_code"))
        if not _BSE_CODE_RE.fullmatch(key):
            raise SourceRowError("invalid_scrip_code", f"BSE scrip code is not well formed: {key!r}")
    name = clean_text(row.value("company_name"))
    if not name:
        raise SourceRowError("missing_name", "Filing has no company name")
    period_raw = clean_text(row.value("period_end"))
    if exchange == "BSE" and _BSE_QUARTER_CODE_RE.fullmatch(period_raw.upper()):
        period_end = _bse_quarter_end(period_raw)
    else:
        period_end = parse_source_date(period_raw, field="period ended")
    scope_raw = clean_text(row.value("scope"))
    # The current browser CSV is an announcement index. It exposes the period
    # and available XBRL links but does not publish a scope column. Treat it as
    # a standalone discovery signal; the subsequent company-page/result
    # validation remains authoritative for accepted financial data.
    scope = normalize_reporting_scope(scope_raw or "standalone")
    broadcast_raw = clean_text(row.value("broadcast")) or clean_text(
        row.value("revised_broadcast")
    )
    broadcast = parse_ist_timestamp(broadcast_raw, field="broadcast date/time")
    isin_raw = clean_text(row.value("isin"))
    return ResultFiling(
        row_position=row.row_position,
        exchange=exchange,
        exchange_key=key,
        isin=normalize_isin(isin_raw) if isin_raw else None,
        company_name_raw=name,
        result_period=period_from_date(period_end),
        period_end_date=period_end,
        reporting_scope=scope,
        broadcast_raw=broadcast_raw,
        broadcast_at_utc=broadcast,
        relating_to_raw=clean_text(row.value("relating_to")) or None,
        audited_raw=clean_text(row.value("audited")) or None,
        cumulative_raw=clean_text(row.value("cumulative")) or None,
        raw_row=_raw_row(row, headers),
        raw_cells=row.cells,
    )


def _bse_quarter_end(value: str) -> str:
    """Resolve BSE fiscal-quarter codes such as JQ2026-2027 to a date."""
    text = clean_text(value).upper()
    match = _BSE_QUARTER_CODE_RE.fullmatch(text)
    if match is None:
        raise SourceRowError(
            "unsupported_period_code", f"BSE quarter code is not quarterly: {value!r}"
        )
    quarter, first_year_raw, second_year_raw = match.groups()
    first_year, second_year = int(first_year_raw), int(second_year_raw)
    if second_year != first_year + 1:
        raise SourceRowError(
            "invalid_period_code", f"BSE fiscal years are not consecutive: {value!r}"
        )
    if quarter == "J":
        return f"{first_year:04d}-06-30"
    if quarter == "S":
        return f"{first_year:04d}-09-30"
    if quarter == "D":
        return f"{first_year:04d}-12-31"
    return f"{second_year:04d}-03-31"


def _parse_bse_integrated(
    content: bytes, *, result_period: str | None = None
) -> ParsedResultFilings:
    """Expand BSE's integrated row into standalone/consolidated filings.

    BSE publishes two physical representations of many source rows (HTML and
    XML) and carries consolidated content beside standalone content. Equivalent
    representations are counted as excluded while the immutable raw response
    preserves them; the two reporting scopes become distinct filing records.
    """
    table = read_json_records(
        content,
        required=_BSE_INTEGRATED_REQUIRED,
        optional=_BSE_INTEGRATED_OPTIONAL,
        records_keys=("Table", "data", "records", "rows", "result"),
    )
    filings: list[ResultFiling] = []
    rejected: list[RejectedRow] = []
    seen: dict[str, int] = {}
    excluded = 0
    for row in table.rows:
        try:
            period_end = _bse_quarter_end(row.value("quarter_code"))
            if result_period is not None and period_from_date(period_end) != result_period:
                excluded += 1
                continue
            scopes: list[str] = []
            if clean_text(row.value("standalone_content")):
                scopes.append("standalone")
            if clean_text(row.value("consolidated_content")):
                scopes.append("consolidated")
            if not scopes:
                raise SourceRowError(
                    "missing_filing_content",
                    "BSE integrated row has neither standalone nor consolidated content",
                )
            for scope in scopes:
                derived = SourceRow(
                    row.row_position,
                    row.cells,
                    {
                        **row.values,
                        "period_end": period_end,
                        "scope": scope,
                    },
                )
                filing = _filing(derived, exchange="BSE", headers=table.headers)
                previous = seen.get(filing.filing_key)
                if previous is not None:
                    excluded += 1
                    continue
                seen[filing.filing_key] = row.row_position
                filings.append(filing)
        except SourceRowError as exc:
            if result_period is not None and exc.reason_code == "unsupported_period_code":
                excluded += 1
                continue
            rejected.append(
                RejectedRow(row.row_position, exc.reason_code, exc.detail, row.cells)
            )
    return ParsedResultFilings(
        BSE_FINANCIAL_RESULTS,
        "BSE",
        table.headers,
        table.encoding,
        tuple(filings),
        tuple(rejected),
        excluded,
    )


def parse_result_filings(
    content: bytes, dataset: str, *, result_period: str | None = None
) -> ParsedResultFilings:
    """Parse one official result feed offline, retaining unusable rows."""
    if dataset not in RESULT_FILING_DATASETS:
        raise ValueError(f"Unknown result-filing dataset: {dataset!r}")
    if result_period is not None and not re.fullmatch(
        r"\d{4}-(?:0[1-9]|1[0-2])", result_period
    ):
        raise ValueError("result_period must be YYYY-MM")
    if (
        dataset == BSE_FINANCIAL_RESULTS
        and is_json_payload(content)
        and b'"quarter_code"' in content
    ):
        return _parse_bse_integrated(content, result_period=result_period)
    if dataset == NSE_FINANCIAL_RESULTS:
        required, optional, exchange = _NSE_REQUIRED, _NSE_OPTIONAL, "NSE"
    else:
        current_bse_export = (
            not is_json_payload(content)
            and b"SECURITY CODE" in content.upper()
            and b"AUDITED/UNAUDITE" in content.upper()
        )
        required, optional, exchange = (
            (_BSE_CURRENT_EXPORT_REQUIRED, _BSE_OPTIONAL, "BSE")
            if current_bse_export else (_BSE_REQUIRED, _BSE_OPTIONAL, "BSE")
        )
    # Both exchanges publish these feeds as JSON records and as delimited
    # exports; the shape is read from the bytes, never assumed from the dataset.
    reader = read_json_records if is_json_payload(content) else read_delimited
    table = reader(content, required=required, optional=optional)

    filings: list[ResultFiling] = []
    rejected: list[RejectedRow] = []
    seen: dict[str, int] = {}
    excluded = 0
    for row in table.rows:
        try:
            filing = _filing(row, exchange=exchange, headers=table.headers)
        except SourceRowError as exc:
            rejected.append(RejectedRow(row.row_position, exc.reason_code, exc.detail, row.cells))
            continue
        if result_period is not None and filing.result_period != result_period:
            excluded += 1
            continue
        previous = seen.get(filing.filing_key)
        if previous is not None:
            rejected.append(
                RejectedRow(
                    row.row_position,
                    "duplicate_filing",
                    f"{filing.filing_key} already appears at row {previous}",
                    row.cells,
                )
            )
            continue
        seen[filing.filing_key] = row.row_position
        filings.append(filing)
    return ParsedResultFilings(
        dataset,
        exchange,
        table.headers,
        table.encoding,
        tuple(filings),
        tuple(rejected),
        excluded,
    )
