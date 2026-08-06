"""Strict parser for saved mfdata family-holdings JSON responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ..amc_workbook.ppfas import isin_status
from .client import holdings_url


MFDATA_PARSER_VERSION = "1.0.0"
MFDATA_NORMALIZATION_RULE_VERSION = "mfdata_holdings_v1"
_MONTH_RE = re.compile(r"^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$")


class MFDataParseError(ValueError):
    """The saved response cannot satisfy the complete M09 contract."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class MFDataHolding:
    source_position: int
    holding_type: str
    holding_type_position: int
    security_name_raw: str
    isin_raw: str | None
    isin_normalized: str | None
    isin_validation_status: str
    quantity_display: str | None
    quantity_decimal: str | None
    market_value_cr_display: str | None
    market_value_cr_decimal: str | None
    market_value_lakh_decimal: str | None
    percentage_of_nav_display: str | None
    percentage_of_nav_decimal: str | None
    change_mom_display: str | None
    change_mom_decimal: str | None
    source_row_json: str


@dataclass(frozen=True)
class MFDataSnapshot:
    provider_family_id: int
    disclosure_month: str
    response_url: str
    holdings: tuple[MFDataHolding, ...]
    parser_version: str = MFDATA_PARSER_VERSION

    @property
    def source(self) -> str:
        return "mfdata"

    @property
    def source_quality(self) -> str:
        return "third_party_unverified"


def _source_json(value: object) -> str:
    def convert(item: object) -> object:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, dict):
            return {str(key): convert(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [convert(inner) for inner in item]
        return item

    return json.dumps(convert(value), sort_keys=True, separators=(",", ":"))


def _decimal_field(
    row: dict[str, Any], field: str, *, required: bool = False
) -> tuple[str | None, str | None]:
    value = row.get(field)
    if value is None:
        if required:
            raise MFDataParseError(
                "missing_numeric_field", f"mfdata row is missing {field!r}", field=field
            )
        return None, None
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise MFDataParseError(
            "invalid_numeric_field", f"mfdata field {field!r} is not decimal-safe",
            field=field,
        )
    display = str(value).strip()
    if display == "":
        if required:
            raise MFDataParseError(
                "missing_numeric_field", f"mfdata row has blank {field!r}", field=field
            )
        return display, None
    try:
        decimal = Decimal(display)
    except InvalidOperation as exc:
        raise MFDataParseError(
            "invalid_numeric_field", f"mfdata field {field!r} is not numeric",
            field=field, display_value=display,
        ) from exc
    if not decimal.is_finite():
        raise MFDataParseError(
            "invalid_numeric_field", f"mfdata field {field!r} is not finite",
            field=field, display_value=display,
        )
    return display, format(decimal, "f")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise MFDataParseError(
            "invalid_identity", f"mfdata {field} must be an integer", field=field
        )
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise MFDataParseError(
            "invalid_identity", f"mfdata {field} must be an integer", field=field
        ) from exc
    if decimal != decimal.to_integral_value():
        raise MFDataParseError(
            "invalid_identity", f"mfdata {field} must be an integer", field=field
        )
    return int(decimal)


def parse_mfdata_holdings(
    raw_bytes: bytes,
    *,
    requested_family_id: int,
    requested_disclosure_month: str,
) -> MFDataSnapshot:
    """Parse one complete saved response without converting numbers to floats."""
    if not _MONTH_RE.fullmatch(requested_disclosure_month):
        raise MFDataParseError(
            "invalid_requested_month", "Requested month must use YYYY-MM"
        )
    try:
        payload = json.loads(
            raw_bytes.decode("utf-8"), parse_float=Decimal, parse_int=Decimal
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MFDataParseError("invalid_json", "Saved mfdata response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise MFDataParseError(
            "unsuccessful_response", "Saved mfdata response status is not success"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MFDataParseError("missing_data", "Saved mfdata response has no data object")
    family_id = _integer(data.get("family_id"), "family_id")
    if family_id != requested_family_id:
        raise MFDataParseError(
            "family_id_mismatch", "Saved mfdata family does not match the requested mapping",
            requested_family_id=requested_family_id, detected_family_id=family_id,
        )
    month = data.get("month")
    if month != requested_disclosure_month:
        raise MFDataParseError(
            "disclosure_month_mismatch",
            "Saved mfdata month does not match the requested official month",
            requested_month=requested_disclosure_month, detected_month=month,
        )

    holdings: list[MFDataHolding] = []
    valid_keys: set[tuple[str, str]] = set()
    source_position = 0
    for holding_type in ("equity", "debt", "other"):
        rows = data.get(holding_type)
        if not isinstance(rows, list):
            raise MFDataParseError(
                "missing_holding_section",
                f"Saved mfdata response does not contain a {holding_type!r} list",
                holding_type=holding_type,
            )
        for holding_type_position, row in enumerate(rows):
            if not isinstance(row, dict):
                raise MFDataParseError(
                    "invalid_holding_row", "Every mfdata holding must be an object",
                    holding_type=holding_type, holding_type_position=holding_type_position,
                )
            name = row.get("name")
            if not isinstance(name, str) or not name.strip():
                raise MFDataParseError(
                    "missing_security_name", "Every mfdata holding needs a security name",
                    holding_type=holding_type, holding_type_position=holding_type_position,
                )
            isin_raw = row.get("isin")
            if isin_raw is not None and not isinstance(isin_raw, str):
                raise MFDataParseError(
                    "invalid_isin_type", "mfdata ISIN must be a string when supplied"
                )
            isin, validation_status = isin_status(isin_raw)
            if validation_status == "valid":
                key = (holding_type, str(isin))
                if key in valid_keys:
                    raise MFDataParseError(
                        "duplicate_holding_key",
                        "A saved mfdata section contains a duplicate valid ISIN",
                        holding_type=holding_type, isin=isin,
                    )
                valid_keys.add(key)
            quantity_display, quantity = _decimal_field(row, "quantity")
            market_display, market_cr = _decimal_field(row, "market_value_cr")
            weight_display, weight = _decimal_field(row, "weight_pct")
            change_display, change = _decimal_field(row, "change_mom")
            market_lakh = (
                None
                if market_cr is None
                else format(Decimal(market_cr) * Decimal("100"), "f")
            )
            holdings.append(
                MFDataHolding(
                    source_position=source_position,
                    holding_type=holding_type,
                    holding_type_position=holding_type_position,
                    security_name_raw=name,
                    isin_raw=isin_raw,
                    isin_normalized=isin,
                    isin_validation_status=validation_status,
                    quantity_display=quantity_display,
                    quantity_decimal=quantity,
                    market_value_cr_display=market_display,
                    market_value_cr_decimal=market_cr,
                    market_value_lakh_decimal=market_lakh,
                    percentage_of_nav_display=weight_display,
                    percentage_of_nav_decimal=weight,
                    change_mom_display=change_display,
                    change_mom_decimal=change,
                    source_row_json=_source_json(row),
                )
            )
            source_position += 1
    return MFDataSnapshot(
        provider_family_id=family_id,
        disclosure_month=month,
        response_url=holdings_url(family_id, month),
        holdings=tuple(holdings),
    )
