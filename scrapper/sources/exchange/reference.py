"""Offline exchange reference CSV parsing; no database or UI dependencies."""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

COLUMNS = (
    "exchange_security_id", "exchange", "segment", "exchange_key", "nse_symbol",
    "bse_scrip_code", "current_isin", "current_name_raw", "current_status",
    "current_version_id", "is_present", "first_observed_date",
    "first_observed_snapshot_id", "last_observed_date", "last_observed_snapshot_id",
    "created_at_utc", "updated_at_utc",
)
INTEGER_COLUMNS = frozenset({"exchange_security_id", "current_version_id", "is_present",
                             "first_observed_snapshot_id", "last_observed_snapshot_id"})
OPTIONAL_COLUMNS = frozenset({"nse_symbol", "bse_scrip_code", "current_isin", "current_version_id"})


def locate_reference_file(root: Path | None = None) -> Path:
    """Prefer packaged resources; retain the legacy root-file location."""
    root = root if root is not None else Path(__file__).resolve().parents[3]
    preferred = root / "resources/exchange/exchange_securities_master.csv"
    return preferred if preferred.is_file() else root / "exchange_securities_master.csv"


def read_reference_rows(path: Path) -> list[tuple]:
    """Validate the complete fixed-schema CSV before any database write."""
    rows = []
    ids, keys = set(), set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError("reference CSV header must match the fixed exchange-security columns")
        for line, row in enumerate(reader, 2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("wrong field count")
                values = {k: (v if v != "" else None) for k, v in row.items()}
                for key in COLUMNS:
                    value = values[key]
                    if value is None and key not in OPTIONAL_COLUMNS:
                        raise ValueError(f"missing {key}")
                    if key in INTEGER_COLUMNS and value is not None:
                        if not value.isascii() or not value.isdigit():
                            raise ValueError(f"invalid integer {key}")
                        values[key] = int(value)
                        if key != "is_present" and values[key] < 1:
                            raise ValueError(f"invalid positive ID {key}")
                if values["exchange"] not in {"NSE", "BSE"}:
                    raise ValueError("invalid exchange")
                if values["segment"] not in {"equity", "sme"}:
                    raise ValueError("invalid segment")
                if values["current_status"] not in {"active", "suspended", "delisted", "absent_from_master", "unknown"}:
                    raise ValueError("invalid current_status")
                if values["is_present"] not in {0, 1}:
                    raise ValueError("invalid is_present")
                nse, bse = values["nse_symbol"], values["bse_scrip_code"]
                if not ((values["exchange"] == "NSE" and nse and bse is None) or
                        (values["exchange"] == "BSE" and bse and nse is None)):
                    raise ValueError("exchange identifier mismatch")
                for key in ("exchange_key", "current_name_raw"):
                    if not values[key].strip():
                        raise ValueError(f"blank {key}")
                for key in ("first_observed_date", "last_observed_date"):
                    date.fromisoformat(values[key])
                for key in ("created_at_utc", "updated_at_utc"):
                    timestamp = datetime.fromisoformat(values[key])
                    if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
                        raise ValueError(f"{key} must have UTC timezone")
                identity = values["exchange_security_id"]
                key = (values["exchange"], values["segment"], values["exchange_key"])
                if identity in ids or key in keys:
                    raise ValueError("duplicate security identity")
                ids.add(identity); keys.add(key)
                rows.append(tuple(values[c] for c in COLUMNS))
            except ValueError as exc:
                raise ValueError(f"reference CSV line {line}: {exc}") from exc
    if not rows:
        raise ValueError("reference CSV contains no rows")
    return rows
