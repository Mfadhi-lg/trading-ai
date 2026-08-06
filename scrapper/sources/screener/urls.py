"""Screener URL construction."""

from ...config import SCREENER_BASE
from .result_screen import normalize_company_path


def company_url(symbol: str, scope: str = "consolidated") -> str:
    normalized = symbol.strip().upper()
    if scope not in {"consolidated", "standalone"}:
        raise ValueError("scope must be consolidated or standalone")
    suffix = "consolidated/" if scope == "consolidated" else ""
    return f"{SCREENER_BASE}/company/{normalized}/{suffix}"


def peer_table_url(warehouse_id: str) -> str:
    normalized = warehouse_id.strip()
    if not normalized.isdigit():
        raise ValueError("Screener warehouse ID must be numeric")
    return f"{SCREENER_BASE}/api/company/{normalized}/peers/"


def company_path_url(company_path: str) -> tuple[str, str]:
    normalized, scope = normalize_company_path(company_path)
    return f"{SCREENER_BASE}{normalized}", scope
