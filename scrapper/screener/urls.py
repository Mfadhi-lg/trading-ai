"""Compatibility wrapper for the original draft module path."""

from ..sources.screener.urls import company_url as _company_url

def company_url(symbol: str, consolidated: bool = True) -> str:
    return _company_url(symbol, "consolidated" if consolidated else "standalone")
