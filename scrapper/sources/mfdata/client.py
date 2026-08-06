"""mfdata endpoint identity helpers; M09 performs no implicit network fetch."""

from __future__ import annotations

from urllib.parse import urlencode


MFDATA_BASE_URL = "https://mfdata.in"


def holdings_url(family_id: int, disclosure_month: str) -> str:
    """Return the exact API URL represented by a saved M09 response."""
    if family_id <= 0:
        raise ValueError("mfdata family_id must be positive")
    return (
        f"{MFDATA_BASE_URL}/api/v1/families/{family_id}/holdings?"
        + urlencode({"month": disclosure_month})
    )
