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
    ExcludedRow,
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
NSE_MII_SECURITY_FILE = "nse_mii_security_file"
SECURITY_MASTER_DATASETS = (
    NSE_EQUITY_MASTER, NSE_SME_MASTER, BSE_SECURITY_MASTER, NSE_MII_SECURITY_FILE,
)

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

# The combined MII security file carries both exchanges' securities in one table.
# Its published shape has not been observed here, so these are declared aliases
# only: binding fails loudly on anything unrecognised rather than reading a
# column by position. Confirm the live headers with `exchange-inspect-dataset`
# and extend these tuples with exactly what NSE publishes.
_MII_REQUIRED = {
    "name": (
        "NAME OF COMPANY", "COMPANY NAME", "SECURITY NAME", "ISSUER NAME", "SC_NAME",
    ),
    "isin": ("ISIN NUMBER", "ISIN", "ISIN CODE", "ISIN NO"),
}
_MII_OPTIONAL = {
    "symbol": ("SYMBOL", "NSE SYMBOL", "TRADING SYMBOL", "NSE TRADING SYMBOL"),
    "scrip_code": (
        "SCRIP CODE", "BSE SCRIP CODE", "SECURITY CODE", "SC_CODE", "BSE CODE",
    ),
    "series": ("SERIES", "NSE SERIES"),
    "instrument": (
        "INSTRUMENT", "INSTRUMENT TYPE", "INSTRUMENT NAME", "SECURITY TYPE",
    ),
    "exchange": ("EXCHANGE", "LISTED ON", "MII", "EXCHANGE NAME", "SOURCE EXCHANGE"),
    "group": ("GROUP", "BSE GROUP", "SC_GROUP"),
    "status": ("STATUS", "SECURITY STATUS"),
    "face_value": ("FACE VALUE",),
    "listing_date": ("DATE OF LISTING", "LISTING DATE"),
    "market_lot": ("MARKET LOT",),
}

# Every exchange key this pipeline can address is one of these two shapes, so a
# row that carries neither is unclassifiable rather than defaulted to an exchange.
_MII_KEY_FIELDS = ("symbol", "scrip_code")
# Classification is never inferred from position in the file; without at least
# one of these columns the file's shape is not the one this parser was written
# against, and it is refused whole.
_MII_CLASS_FIELDS = ("series", "instrument")
_MII_NSE_MARKERS = frozenset({"NSE", "NSEIL", "NSE INDIA", "N"})
_MII_BSE_MARKERS = frozenset({"BSE", "BSE LTD", "BSE LIMITED", "B"})
_MII_MARKER_SPLIT = re.compile(r"[^A-Z]+")

# Series and instrument values that name an equity security. Anything outside
# both sets is excluded from the registry, not rejected: the row is fine, it just
# is not something this pipeline collects.
_EQUITY_SERIES = frozenset({"EQ", "BE", "BZ", "IQ", "SM", "ST"})
_SME_SERIES = frozenset({"SM", "ST"})
_EQUITY_INSTRUMENTS = frozenset(
    {"equity", "equities", "equity shares", "equity share", "ordinary shares", "es"}
)
# Ordered because the first marker found names the class; the non-equity markers
# are tested before the equity ones so "equity preference shares" is a preference
# share rather than an equity.
_EXCLUDED_INSTRUMENT_MARKERS = (
    ("rights entitlement", "rights_entitlement"),
    ("preference", "preference_share"),
    ("warrant", "warrant"),
    ("infrastructure investment trust", "invit"),
    ("invit", "invit"),
    ("real estate investment trust", "reit"),
    ("reit", "reit"),
    ("exchange traded fund", "etf"),
    ("etf", "etf"),
    ("mutual fund", "mutual_fund"),
    ("sovereign gold", "government_security"),
    ("government", "government_security"),
    ("treasury", "government_security"),
    ("g-sec", "government_security"),
    ("state development loan", "government_security"),
    ("debenture", "debt"),
    ("commercial paper", "debt"),
    ("securitised", "debt"),
    ("securitized", "debt"),
    ("bond", "debt"),
    ("debt", "debt"),
)
_EXCLUDED_SERIES = {
    "GB": "government_security",
    "GS": "government_security",
    "TB": "government_security",
    "RR": "rights_entitlement",
    "RE": "rights_entitlement",
    "MF": "mutual_fund",
    "IV": "invit",
    "RT": "reit",
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


class InstrumentExcluded(Exception):
    """A row this pipeline deliberately does not collect. Counted, never rejected."""

    def __init__(self, instrument_class: str, detail: str) -> None:
        super().__init__(f"{instrument_class}: {detail}")
        self.instrument_class = instrument_class
        self.detail = detail


@dataclass(frozen=True)
class ParsedSecurityMaster:
    dataset: str
    exchange: str
    headers: tuple[str, ...]
    encoding: str
    records: tuple[SecurityRecord, ...]
    rejected: tuple[RejectedRow, ...]
    excluded: tuple[ExcludedRow, ...] = ()

    @property
    def accepted_row_count(self) -> int:
        return len(self.records)

    @property
    def rejected_row_count(self) -> int:
        return len(self.rejected)

    @property
    def excluded_row_count(self) -> int:
        return len(self.excluded)

    @property
    def segments(self) -> tuple[tuple[str, str], ...]:
        """The (exchange, segment) pairs this file actually carries rows for."""
        return tuple(
            sorted({(record.exchange, record.segment) for record in self.records})
        )


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


def _markers(value: str) -> set[str]:
    """The exchange names a combined-file cell states, in any punctuation."""
    text = clean_text(value).upper()
    if not text:
        return set()
    return {token for token in _MII_MARKER_SPLIT.split(text) if token} | {text}


def classify_instrument(series: str, instrument: str) -> str:
    """Name the instrument class of one row: 'equity' or the class to exclude.

    Positive evidence only. A row is collected because its own series or
    instrument text says it is an equity, never because nothing said otherwise —
    an unrecognised class is kept out of the universe and reported, so a new
    instrument type on the exchange shows up as a number to look at instead of
    quietly becoming a company.
    """
    series_code = clean_text(series).upper()
    instrument_text = clean_text(instrument).lower()
    if series_code in _EXCLUDED_SERIES:
        return _EXCLUDED_SERIES[series_code]
    for marker, instrument_class in _EXCLUDED_INSTRUMENT_MARKERS:
        if marker in instrument_text:
            return instrument_class
    if series_code in _EQUITY_SERIES:
        return "equity"
    if instrument_text in _EQUITY_INSTRUMENTS:
        return "equity"
    return "unrecognised"


def _mii_record(row: SourceRow) -> SecurityRecord:
    """Build one record from the combined file, classified by its own columns.

    A row is NSE-listed or BSE-exclusive because of what it says about itself.
    Nothing falls back to a default exchange: a row that names neither a usable
    NSE symbol nor a usable BSE scrip code is rejected as unclassifiable.
    """
    instrument = clean_text(row.value("instrument")) or None
    series = clean_text(row.value("series")).upper()
    instrument_class = classify_instrument(series, instrument or "")
    if instrument_class != "equity":
        raise InstrumentExcluded(
            instrument_class,
            f"series={series!r} instrument={instrument!r} is not an equity security",
        )
    name = clean_text(row.value("name"))
    if not name:
        raise SourceRowError("missing_name", "combined-file security name is blank")
    isin = normalize_isin(row.value("isin"))

    symbol = clean_text(row.value("symbol")).upper()
    scrip_code = clean_text(row.value("scrip_code"))
    has_symbol = bool(_NSE_SYMBOL_RE.fullmatch(symbol))
    has_scrip_code = bool(_BSE_CODE_RE.fullmatch(scrip_code))
    markers = _markers(row.value("exchange"))
    names_nse = bool(markers & _MII_NSE_MARKERS)
    names_bse = bool(markers & _MII_BSE_MARKERS)
    # An explicit exchange column is the strongest statement the row makes, so it
    # decides first; the key shapes decide only when the file does not say.
    if markers:
        nse_listed = names_nse and has_symbol
        bse_exclusive = names_bse and not names_nse and has_scrip_code
    else:
        nse_listed = has_symbol
        bse_exclusive = not has_symbol and has_scrip_code
    if not nse_listed and not bse_exclusive:
        raise SourceRowError(
            "unclassified_exchange",
            "row names neither a usable NSE symbol nor a BSE-exclusive scrip code: "
            f"symbol={symbol!r} scrip_code={scrip_code!r} exchange={sorted(markers)}",
        )

    group = clean_text(row.value("group")).upper() or None
    sme = series in _SME_SERIES or (group in _BSE_SME_GROUPS if group else False)
    listing = row.value("listing_date")
    status_raw = clean_text(row.value("status")) or None
    status = _BSE_STATUS.get((status_raw or "").lower(), "active")
    if nse_listed:
        return SecurityRecord(
            row_position=row.row_position,
            exchange="NSE",
            segment="sme" if sme else "equity",
            exchange_key=symbol,
            nse_symbol=symbol,
            # The combined file states the BSE code of a dual-listed security;
            # it is retained as an attribute and never used as an NSE key.
            bse_scrip_code=None,
            isin=isin,
            name_raw=name,
            status=status,
            status_raw=status_raw,
            series_or_group=series or None,
            instrument_raw=instrument,
            face_value_raw=row.value("face_value") or None,
            listing_date=(
                parse_source_date(listing, field="date of listing") if listing else None
            ),
            attributes=_extras(row, ("market_lot", "scrip_code", "exchange")),
            raw_cells=row.cells,
        )
    return SecurityRecord(
        row_position=row.row_position,
        exchange="BSE",
        segment="sme" if sme else "equity",
        exchange_key=scrip_code,
        nse_symbol=None,
        bse_scrip_code=scrip_code,
        isin=isin,
        name_raw=name,
        status=status,
        status_raw=status_raw,
        series_or_group=group or series or None,
        instrument_raw=instrument,
        face_value_raw=row.value("face_value") or None,
        listing_date=(
            parse_source_date(listing, field="date of listing") if listing else None
        ),
        attributes=_extras(row, ("market_lot", "exchange")),
        raw_cells=row.cells,
    )


def _require_bound(table: ParsedTable, *, any_of: tuple[str, ...], purpose: str) -> None:
    """Refuse a combined file whose shape this parser was not written against."""
    bound = {field for row in table.rows[:1] for field in row.values}
    if not table.rows or not bound & set(any_of):
        raise ValueError(
            f"{purpose} needs one of {list(any_of)}; headers={list(table.headers)}"
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
    elif dataset == NSE_MII_SECURITY_FILE:
        table = reader(content, required=_MII_REQUIRED, optional=_MII_OPTIONAL)
        _require_bound(
            table, any_of=_MII_KEY_FIELDS, purpose="the combined MII security file"
        )
        _require_bound(
            table,
            any_of=_MII_CLASS_FIELDS,
            purpose="classifying combined-file instruments",
        )
        # NSE publishes the combined file; the rows inside it name their own
        # exchange, and the sweep scope comes from the dataset registry.
        exchange, build = "NSE", _mii_record
    else:
        table = reader(content, required=_NSE_REQUIRED, optional=_NSE_OPTIONAL)
        exchange = "NSE"
        segment = "sme" if dataset == NSE_SME_MASTER else "equity"

        def build(row: SourceRow) -> SecurityRecord:
            return _nse_record(row, segment=segment)

    records: list[SecurityRecord] = []
    rejected: list[RejectedRow] = []
    excluded: list[ExcludedRow] = []
    # Keyed by exchange too: a combined file carries both exchanges' keys, and a
    # six-digit BSE code is not a duplicate of an identically spelled NSE symbol.
    seen: dict[tuple[str, str], int] = {}
    for row in table.rows:
        try:
            record = build(row)
        except InstrumentExcluded as exc:
            excluded.append(
                ExcludedRow(row.row_position, exc.instrument_class, exc.detail, row.cells)
            )
            continue
        except SourceRowError as exc:
            rejected.append(RejectedRow(row.row_position, exc.reason_code, exc.detail, row.cells))
            continue
        previous = seen.get((record.exchange, record.exchange_key))
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
        seen[(record.exchange, record.exchange_key)] = row.row_position
        records.append(record)
    if not records:
        raise ValueError(f"{dataset} contains no usable securities")
    return ParsedSecurityMaster(
        dataset, exchange, table.headers, table.encoding, tuple(records),
        tuple(rejected), tuple(excluded),
    )
