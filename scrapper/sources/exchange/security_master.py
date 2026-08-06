"""Source-faithful parsing of the official NSE and BSE security masters.

The masters are the universe of record. A company enters the pipeline because an
exchange lists its security, not because a screen happened to show it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Mapping

from ...core.nums import clean_text
from .tabular import (
    ParsedTable,
    RejectedRow,
    SourceRow,
    SourceRowError,
    is_json_payload,
    normalize_isin,
    parse_source_date,
    read_delimited,
    read_json_records,
)


NSE_EQUITY_MASTER = "nse_equity_master"
NSE_SME_MASTER = "nse_sme_master"
BSE_SECURITY_MASTER = "bse_security_master"
SECURITY_MASTER_DATASETS = (NSE_EQUITY_MASTER, NSE_SME_MASTER, BSE_SECURITY_MASTER)

_NSE_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,24}$")
_BSE_CODE_RE = re.compile(r"^[0-9]{6}$")
_BSE_SME_GROUPS = frozenset({"M", "MT", "MS"})
_BSE_STATUS = {
    "active": "active",
    "listed": "active",
    "suspended": "suspended",
    "delisted": "delisted",
}

_NSE_REQUIRED = {
    "symbol": ("SYMBOL",),
    "name": ("NAME OF COMPANY", "COMPANY NAME", "SECURITY NAME"),
    "isin": ("ISIN NUMBER", "ISIN", "ISIN CODE", "ISIN NO"),
}
_NSE_OPTIONAL = {
    "series": ("SERIES",),
    "listing_date": ("DATE OF LISTING", "LISTING DATE"),
    "face_value": ("FACE VALUE",),
    "paid_up_value": ("PAID UP VALUE",),
    "market_lot": ("MARKET LOT",),
}
# The mixed-case aliases are the published BSE JSON API keys; the spaced aliases
# are the same logical columns as published in the delimited exports.
_BSE_REQUIRED = {
    "scrip_code": ("SECURITY CODE", "SCRIP CODE", "SC_CODE", "SCRIP_CD"),
    "name": ("SECURITY NAME", "SC_NAME", "Scrip_Name"),
    "status": ("STATUS", "SC_STATUS"),
    "isin": ("ISIN NO", "ISIN NUMBER", "ISIN", "ISIN CODE"),
    "instrument": ("INSTRUMENT", "SECURITY TYPE", "SC_TYPE", "Segment"),
}
_BSE_OPTIONAL = {
    "security_id": ("SECURITY ID", "SCRIP ID", "SC_ID"),
    "issuer_name": ("ISSUER NAME", "Issuer_Name"),
    "group": ("GROUP", "SC_GROUP"),
    "face_value": ("FACE VALUE", "SC_FACEVALUE"),
    "industry": ("INDUSTRY", "INDUSTRY NEW NAME"),
    "sector": ("SECTOR NAME",),
}


@dataclass(frozen=True)
class SecurityRecord:
    row_position: int
    exchange: str
    segment: str
    exchange_key: str
    nse_symbol: str | None
    bse_scrip_code: str | None
    isin: str | None
    name_raw: str
    status: str
    status_raw: str | None
    series_or_group: str | None
    instrument_raw: str | None
    face_value_raw: str | None
    listing_date: str | None
    attributes: Mapping[str, str] = field(default_factory=dict)
    raw_cells: tuple[str, ...] = ()

    @property
    def row_sha256(self) -> str:
        payload = json.dumps(
            {
                "exchange": self.exchange,
                "segment": self.segment,
                "exchange_key": self.exchange_key,
                "isin": self.isin,
                "name_raw": self.name_raw,
                "status": self.status,
                "series_or_group": self.series_or_group,
                "instrument_raw": self.instrument_raw,
                "face_value_raw": self.face_value_raw,
                "listing_date": self.listing_date,
                "attributes": dict(sorted(self.attributes.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParsedSecurityMaster:
    dataset: str
    exchange: str
    headers: tuple[str, ...]
    encoding: str
    records: tuple[SecurityRecord, ...]
    rejected: tuple[RejectedRow, ...]

    @property
    def accepted_row_count(self) -> int:
        return len(self.records)

    @property
    def rejected_row_count(self) -> int:
        return len(self.rejected)


def _extras(row: SourceRow, fields: tuple[str, ...]) -> dict[str, str]:
    return {name: row.value(name) for name in fields if row.value(name)}


def _nse_record(row: SourceRow, *, segment: str) -> SecurityRecord:
    symbol = clean_text(row.value("symbol")).upper()
    if not _NSE_SYMBOL_RE.fullmatch(symbol):
        raise SourceRowError("invalid_symbol", f"NSE symbol is not well formed: {symbol!r}")
    name = clean_text(row.value("name"))
    if not name:
        raise SourceRowError("missing_name", "NSE security name is blank")
    isin = normalize_isin(row.value("isin"))
    listing = row.value("listing_date")
    return SecurityRecord(
        row_position=row.row_position,
        exchange="NSE",
        segment=segment,
        exchange_key=symbol,
        nse_symbol=symbol,
        bse_scrip_code=None,
        isin=isin,
        name_raw=name,
        status="active",
        status_raw=None,
        series_or_group=clean_text(row.value("series")).upper() or None,
        instrument_raw=None,
        face_value_raw=row.value("face_value") or None,
        listing_date=parse_source_date(listing, field="date of listing") if listing else None,
        attributes=_extras(row, ("paid_up_value", "market_lot")),
        raw_cells=row.cells,
    )


def _bse_record(row: SourceRow) -> SecurityRecord:
    code = clean_text(row.value("scrip_code"))
    if not _BSE_CODE_RE.fullmatch(code):
        raise SourceRowError("invalid_scrip_code", f"BSE scrip code is not well formed: {code!r}")
    name = clean_text(row.value("name"))
    if not name:
        raise SourceRowError("missing_name", "BSE security name is blank")
    status_raw = clean_text(row.value("status"))
    status = _BSE_STATUS.get(status_raw.lower(), "unknown")
    if status == "unknown" and status_raw:
        raise SourceRowError("unknown_status", f"BSE status is unrecognised: {status_raw!r}")
    group = clean_text(row.value("group")).upper() or None
    isin_raw = clean_text(row.value("isin"))
    # A delisted or suspended BSE scrip may legitimately carry no ISIN; an
    # active one without a usable ISIN cannot join the ISIN-keyed registry.
    if not isin_raw and status in {"delisted", "suspended"}:
        isin = None
    else:
        isin = normalize_isin(isin_raw)
    return SecurityRecord(
        row_position=row.row_position,
        exchange="BSE",
        segment="sme" if group in _BSE_SME_GROUPS else "equity",
        exchange_key=code,
        nse_symbol=None,
        bse_scrip_code=code,
        isin=isin,
        name_raw=name,
        status=status,
        status_raw=status_raw or None,
        series_or_group=group,
        instrument_raw=clean_text(row.value("instrument")) or None,
        face_value_raw=row.value("face_value") or None,
        listing_date=None,
        attributes=_extras(row, ("security_id", "issuer_name", "industry", "sector")),
        raw_cells=row.cells,
    )


def parse_security_master(content: bytes, dataset: str) -> ParsedSecurityMaster:
    """Parse one dated security-master file without network or database access."""
    if dataset not in SECURITY_MASTER_DATASETS:
        raise ValueError(f"Unknown security-master dataset: {dataset!r}")
    # The published masters exist as delimited exports and as JSON endpoints;
    # the shape is read from the bytes, never assumed from the dataset.
    reader = read_json_records if is_json_payload(content) else read_delimited
    if dataset == BSE_SECURITY_MASTER:
        table: ParsedTable = reader(
            content, required=_BSE_REQUIRED, optional=_BSE_OPTIONAL
        )
        exchange, build = "BSE", _bse_record
    else:
        table = reader(content, required=_NSE_REQUIRED, optional=_NSE_OPTIONAL)
        exchange = "NSE"
        segment = "sme" if dataset == NSE_SME_MASTER else "equity"

        def build(row: SourceRow) -> SecurityRecord:
            return _nse_record(row, segment=segment)

    records: list[SecurityRecord] = []
    rejected: list[RejectedRow] = []
    seen: dict[str, int] = {}
    for row in table.rows:
        try:
            record = build(row)
        except SourceRowError as exc:
            rejected.append(RejectedRow(row.row_position, exc.reason_code, exc.detail, row.cells))
            continue
        previous = seen.get(record.exchange_key)
        if previous is not None:
            rejected.append(
                RejectedRow(
                    row.row_position,
                    "duplicate_key",
                    f"{record.exchange_key} already appears at row {previous}",
                    row.cells,
                )
            )
            continue
        seen[record.exchange_key] = row.row_position
        records.append(record)
    if not records:
        raise ValueError(f"{dataset} contains no usable securities")
    return ParsedSecurityMaster(
        dataset, exchange, table.headers, table.encoding, tuple(records), tuple(rejected)
    )
