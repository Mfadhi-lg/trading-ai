"""Deterministic delimited-file reading shared by the exchange adapters.

Columns are resolved from header evidence, never from fixed offsets, except for
the officially headerless NSE change files, which declare an explicit versioned
column contract instead. Every row keeps its exact source cells so a rejected
row can be reviewed against the immutable snapshot it came from.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

from ...core.nums import clean_text


# Exchange broadcast and effective timestamps are published in IST. The offset
# is fixed and has no daylight rule, so it is applied without a tz database.
IST = timezone(timedelta(hours=5, minutes=30))

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# Four-digit years bind first. The two-digit month-name forms exist only because
# the NSE SME master publishes "03-Aug-26"; they are last so an unambiguous
# four-digit year is never reinterpreted, and only the month-name shapes are
# admitted so no all-numeric date becomes ambiguous.
_DATE_FORMATS = (
    "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
    "%d-%b-%y", "%d-%B-%y",
)
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f",) + tuple(
    f"{day} {clock}"
    for day in _DATE_FORMATS
    for clock in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")
) + ("%Y-%m-%dT%H:%M:%S",)


class SourceRowError(ValueError):
    """A single source row that must be retained and reviewed, not dropped."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class SourceRow:
    row_position: int
    cells: tuple[str, ...]
    values: Mapping[str, str]

    def value(self, field: str) -> str:
        return self.values.get(field, "")


@dataclass(frozen=True)
class RejectedRow:
    row_position: int
    reason_code: str
    reason_detail: str
    cells: tuple[str, ...]


@dataclass(frozen=True)
class ParsedTable:
    headers: tuple[str, ...]
    encoding: str
    rows: tuple[SourceRow, ...]


def normalize_header(value: str) -> str:
    text = clean_text(value).lower()
    text = text.replace("–", "-").replace("’", "'")
    # NSE publishes one logical column both spaced ("NAME OF COMPANY") and
    # underscored ("NAME_OF_COMPANY"); both bind to the same declared alias.
    text = clean_text(text.replace("_", " "))
    return text.strip(" .:")


def decode_source(content: bytes) -> tuple[str, str]:
    """Decode with the first exactly-matching encoding; never lossy-decode."""
    for encoding in _ENCODINGS:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Exchange source file is not UTF-8 or CP1252 decodable")


def _bind_fields(
    headers: Sequence[str],
    *,
    required: Mapping[str, Sequence[str]],
    optional: Mapping[str, Sequence[str]] | None,
) -> dict[str, int]:
    """Bind declared fields to exact column positions from header evidence."""
    normalized = [normalize_header(cell) for cell in headers]
    duplicates = {name for name in normalized if name and normalized.count(name) > 1}
    if duplicates:
        raise ValueError(f"Duplicate source headers: {sorted(duplicates)}")

    binding: dict[str, int] = {}
    for field, aliases in {**required, **(optional or {})}.items():
        matches = [
            index
            for index, name in enumerate(normalized)
            if name in {normalize_header(alias) for alias in aliases}
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous source column for {field!r}: {matches}")
        if matches:
            binding[field] = matches[0]
        elif field in required:
            raise ValueError(
                f"Required source column for {field!r} is missing; headers={list(headers)}"
            )
    return binding


def is_json_payload(content: bytes) -> bool:
    """True when the source bytes are a JSON document rather than a table."""
    return content.lstrip()[:1] in (b"[", b"{")


def _json_cell(value: object) -> str:
    """Render one JSON value as its source-faithful cell text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    # A nested structure is retained verbatim rather than flattened away.
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def read_json_records(
    content: bytes,
    *,
    required: Mapping[str, Sequence[str]],
    optional: Mapping[str, Sequence[str]] | None = None,
    records_keys: Sequence[str] = ("data", "records", "rows", "result"),
) -> ParsedTable:
    """Read an official JSON record feed under the same header-bound contract.

    Object keys are the headers, so field binding, duplicate-header rejection and
    required-column enforcement behave exactly as they do for a delimited file.
    """
    text, encoding = decode_source(content)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Exchange source is not valid JSON: {exc}") from exc
    if isinstance(payload, Mapping):
        for key in records_keys:
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            raise ValueError(
                f"JSON payload has no record list; keys={sorted(map(str, payload))}"
            )
    if not isinstance(payload, list) or not payload:
        raise ValueError("Exchange source file contains no rows")

    headers: list[str] = []
    seen_headers: set[str] = set()
    for record in payload:
        if not isinstance(record, Mapping):
            raise ValueError("JSON record feed contains a non-object record")
        for key in record:
            name = str(key)
            if name not in seen_headers:
                seen_headers.add(name)
                headers.append(name)
    if not headers:
        raise ValueError("Exchange source file contains no rows")

    header_tuple = tuple(headers)
    binding = _bind_fields(header_tuple, required=required, optional=optional)
    rows: list[SourceRow] = []
    for position, record in enumerate(payload, start=1):
        cells = tuple(_json_cell(record.get(name)) for name in header_tuple)
        rows.append(
            SourceRow(
                position,
                cells,
                {field: clean_text(cells[index]) for field, index in binding.items()},
            )
        )
    return ParsedTable(header_tuple, encoding, tuple(rows))


def read_delimited(
    content: bytes,
    *,
    required: Mapping[str, Sequence[str]],
    optional: Mapping[str, Sequence[str]] | None = None,
    delimiter: str = ",",
) -> ParsedTable:
    """Read a headed delimited file and bind declared fields to exact columns."""
    text, encoding = decode_source(content)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records = [row for row in reader if any(cell.strip() for cell in row)]
    if not records:
        raise ValueError("Exchange source file contains no rows")
    headers = tuple(str(cell) for cell in records[0])
    binding = _bind_fields(headers, required=required, optional=optional)

    rows: list[SourceRow] = []
    for position, record in enumerate(records[1:], start=1):
        cells = tuple(str(cell) for cell in record)
        if len(cells) != len(headers):
            raise ValueError(
                f"Row {position} has {len(cells)} cells; expected {len(headers)}"
            )
        rows.append(
            SourceRow(
                position,
                cells,
                {field: clean_text(cells[index]) for field, index in binding.items()},
            )
        )
    return ParsedTable(headers, encoding, tuple(rows))


def read_fixed_columns(
    content: bytes,
    *,
    columns: Sequence[str],
    delimiter: str = ",",
    header_hints: Iterable[str] = (),
) -> ParsedTable:
    """Read an officially headerless file under an explicit column contract."""
    text, encoding = decode_source(content)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records = [row for row in reader if any(cell.strip() for cell in row)]
    if not records:
        raise ValueError("Exchange source file contains no rows")
    hints = {normalize_header(hint) for hint in header_hints}
    first = [normalize_header(cell) for cell in records[0]]
    if hints and hints.issubset(set(first)):
        records = records[1:]
    rows: list[SourceRow] = []
    for position, record in enumerate(records, start=1):
        cells = tuple(str(cell) for cell in record)
        if len(cells) != len(columns):
            raise ValueError(
                f"Row {position} has {len(cells)} cells; expected {len(columns)}"
            )
        rows.append(
            SourceRow(
                position,
                cells,
                {name: clean_text(cells[index]) for index, name in enumerate(columns)},
            )
        )
    return ParsedTable(tuple(columns), encoding, tuple(rows))


def normalize_isin(value: str) -> str:
    """Validate structure and the ISO 6166 check digit; reject anything else."""
    text = clean_text(value).upper().replace(" ", "")
    if not _ISIN_RE.fullmatch(text):
        raise SourceRowError("invalid_isin", f"ISIN is not well formed: {value!r}")
    digits = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in text[:-1]
    )
    total = 0
    for index, character in enumerate(reversed(digits)):
        digit = int(character)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    if (10 - total % 10) % 10 != int(text[-1]):
        raise SourceRowError("invalid_isin", f"ISIN check digit fails: {value!r}")
    return text


def parse_source_date(value: str, *, field: str) -> str:
    text = clean_text(value)
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise SourceRowError("invalid_date", f"{field} is not a known date: {value!r}")


def parse_ist_timestamp(value: str, *, field: str) -> str:
    """Return an exact UTC instant for an IST-published exchange timestamp."""
    text = clean_text(value)
    for pattern in _DATETIME_FORMATS:
        try:
            naive = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return naive.replace(tzinfo=IST).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
    for pattern in _DATE_FORMATS:
        try:
            day = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        # A date-only broadcast value is anchored at the start of its IST day so
        # the watermark can never skip filings published earlier that day.
        return datetime(
            day.year, day.month, day.day, tzinfo=IST
        ).astimezone(timezone.utc).isoformat(timespec="seconds")
    raise SourceRowError("invalid_timestamp", f"{field} is not a known timestamp: {value!r}")


def period_from_date(period_end: str) -> str:
    return period_end[:7]


def effective_date_or_today(value: str | None, *, now_utc: str) -> str:
    if value:
        text = clean_text(value)
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError(f"effective-date must be YYYY-MM-DD: {value!r}") from exc
    return now_utc[:10]
