"""Screener URL construction."""

from ...config import SCREENER_BASE
from .result_screen import normalize_company_path


def company_url(symbol: str, scope: str = "standalone") -> str:
    normalized = symbol.strip().upper()
    if scope != "standalone":
        raise ValueError("financial Screener URL scope must be standalone")
    return f"{SCREENER_BASE}/company/{normalized}/"


def peer_table_url(warehouse_id: str) -> str:
    normalized = warehouse_id.strip()
    if not normalized.isdigit():
        raise ValueError("Screener warehouse ID must be numeric")
    return f"{SCREENER_BASE}/api/company/{normalized}/peers/"


def quick_ratios_url(warehouse_id: str) -> str:
    """URL for the AJAX-loaded custom quick ratios fragment."""
    normalized = warehouse_id.strip()
    if not normalized.isdigit():
        raise ValueError("Screener warehouse ID must be numeric")
    return f"{SCREENER_BASE}/api/company/{normalized}/quick_ratios/"


def commentary_url(company_id: str) -> str:
    """URL for the wiki / Key Insights commentary modal fragment."""
    normalized = company_id.strip()
    if not normalized.isdigit():
        raise ValueError("Screener company ID must be numeric")
    return f"{SCREENER_BASE}/wiki/company/{normalized}/commentary/v2/"


def company_path_url(company_path: str) -> tuple[str, str]:
    normalized, scope = normalize_company_path(company_path)
    if scope != "standalone":
        raise ValueError("consolidated Screener paths are not financial ingestion targets")
    return f"{SCREENER_BASE}{normalized}", scope
