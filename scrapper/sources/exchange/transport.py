"""Official exchange transport and response policy; no acquisition at import."""
from __future__ import annotations
from typing import Any
from urllib.parse import urlsplit, urljoin
import requests
import certifi
from ...config import REQUEST_TIMEOUT, USER_AGENT
from ...core.http import FetchResponse
from ...core.access_policy import refuse_blocked_terms_target

EXCHANGE_HOSTS = {
    "NSE": {
        "www.nseindia.com": ("/api/", "/content/", "/static/"),
        "nsearchives.nseindia.com": ("/content/", "/corporate/", "/emerge/"),
    },
    "BSE": {
        "www.bseindia.com": (
            "/downloads/", "/corporates/", "/markets/", "/BSEDATA/", "/xml-data/"
        ),
        "api.bseindia.com": ("/BseIndiaAPI/", "/RealTimeBseIndiaAPI/"),
    },
}
_EXCHANGE_REFERERS = {
    "NSE": "https://www.nseindia.com/",
    "BSE": "https://www.bseindia.com/",
}
# Leading bytes of a gzip member and of a zip local file header. Duplicated from
# `sources.exchange.tabular` rather than imported so the core HTTP layer keeps
# depending on nothing above it; both places are asserted against each other in
# the exchange tests.
GZIP_MAGIC = b"\x1f\x8b"
ZIP_MAGIC = b"PK\x03\x04"
ZIP_EMPTY_MAGIC = b"PK\x05\x06"
_EXCHANGE_MEDIA_TYPES = (
    "text/csv", "text/plain", "application/csv", "application/json",
    "application/octet-stream", "application/vnd.ms-excel", "application/zip",
    "application/gzip", "application/x-gzip", "application/x-zip-compressed",
)



def validate_exchange_dataset_url(url: str, exchange: str, *, EXCHANGE_HOSTS=EXCHANGE_HOSTS, urlsplit=urlsplit) -> str:
    """Allow only official published dataset URLs for the named exchange."""
    hosts = EXCHANGE_HOSTS.get(exchange.upper())
    if hosts is None:
        raise ValueError(f"Unknown exchange: {exchange!r}")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("Exchange dataset URL must be a plain HTTPS URL")
    prefixes = hosts.get(parts.hostname or "")
    if prefixes is None:
        raise ValueError(f"{parts.hostname!r} is not an allowed {exchange.upper()} host")
    if not parts.path.startswith(prefixes):
        raise ValueError(f"{parts.path!r} is not an allowed {exchange.upper()} dataset path")
    return url


def _classify_exchange_response(response: requests.Response, *, GZIP_MAGIC=GZIP_MAGIC, ZIP_EMPTY_MAGIC=ZIP_EMPTY_MAGIC, ZIP_MAGIC=ZIP_MAGIC, _EXCHANGE_MEDIA_TYPES=_EXCHANGE_MEDIA_TYPES) -> str:
    if response.status_code >= 400:
        # A dated artifact that is not there is the exchange saying it has not
        # published one, which is a different fact from being turned away or
        # from the endpoint breaking. The scheduler needs to tell them apart.
        if response.status_code in {404, 410}:
            return "not_found"
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    if not response.content:
        return "rejected"
    declared = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if declared and not any(declared == media for media in _EXCHANGE_MEDIA_TYPES):
        return "rejected"
    # A compressed archive is binary: decoding it as text produces arbitrary
    # bytes that must not be sniffed for HTML. The magic bytes settle it first,
    # and a real interstitial is never a valid gzip or zip container.
    if response.content.startswith((GZIP_MAGIC, ZIP_MAGIC, ZIP_EMPTY_MAGIC)):
        return "ok"
    sample = response.content[:4096].decode("utf-8", errors="ignore").lstrip().lower()
    # An interstitial, captcha or error page is HTML whatever it claims to be.
    if sample.startswith(("<!doctype html", "<html", "<?xml")):
        return "rejected"
    if "captcha" in sample or "access denied" in sample:
        return "rejected"
    return "ok"


def fetch_exchange_dataset(
    url: str,
    exchange: str,
    *,
    referer: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
    FetchResponse=FetchResponse, USER_AGENT=USER_AGENT, _EXCHANGE_REFERERS=_EXCHANGE_REFERERS, _classify_exchange_response=_classify_exchange_response, certifi=certifi, requests=requests, urlsplit=urlsplit, validate_exchange_dataset_url=validate_exchange_dataset_url,
) -> FetchResponse:
    """Fetch one official exchange dataset file without any session material."""
    validate_exchange_dataset_url(url, exchange)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/csv,application/json,text/plain,*/*",
        }
    )
    # Both exchanges serve their dataset endpoints only to requests originating
    # from their own public site; without that origin BSE returns an HTML
    # interstitial. This carries no session or credential material.
    default_referer = _EXCHANGE_REFERERS[exchange.upper()]
    if (
        referer is None
        and exchange.upper() == "BSE"
        and "/Corp_FinanceResult_ng_new/" in urlsplit(url).path
    ):
        # This public API returns an empty object for its generic home-page
        # referer; the results application itself uses this exact first-party
        # route. No cookie or other session material is attached.  The public
        # results page is case-insensitively served at this `.aspx` route;
        # omitting the extension makes the API return an empty object.
        default_referer = "https://www.bseindia.com/corporates/comp_results.aspx"
    session.headers["Referer"] = referer or default_referer
    referer_parts = urlsplit(session.headers["Referer"])
    session.headers["Origin"] = f"{referer_parts.scheme}://{referer_parts.netloc}"
    response = session.get(
        url, timeout=timeout, allow_redirects=True, verify=certifi.where()
    )
    return FetchResponse(
        requested_url=url,
        final_url=response.url,
        status_code=response.status_code,
        content=response.content,
        content_type=response.headers.get("Content-Type"),
        encoding=response.encoding,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
        auth_mode="public",
        fetch_status=_classify_exchange_response(response),
    )


def fetch_exchange_recovery_surface(
    url: str,
    exchange: str,
    *,
    referer: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
    max_redirects: int = 3,
    session: requests.Session | Any | None = None,
    FetchResponse=FetchResponse, USER_AGENT=USER_AGENT, _EXCHANGE_REFERERS=_EXCHANGE_REFERERS, _classify_exchange_response=_classify_exchange_response, certifi=certifi, refuse_blocked_terms_target=refuse_blocked_terms_target, requests=requests, urljoin=urljoin, urlsplit=urlsplit, validate_exchange_dataset_url=validate_exchange_dataset_url,
) -> FetchResponse:
    """Fetch DA-06 exchange search metadata with per-hop redirect policy.

    Unlike the legacy static-dataset client, this path never lets ``requests``
    follow a redirect implicitly.  Every next hop must remain an allowlisted
    official exchange URL and is checked against BLOCKED_TERMS before a second
    request can occur.
    """

    if max_redirects < 0:
        raise ValueError("max_redirects cannot be negative")
    exchange = exchange.upper()
    validate_exchange_dataset_url(url, exchange)
    owned = session is None
    session = session or requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    default_referer = referer or _EXCHANGE_REFERERS[exchange]
    session.headers["Referer"] = default_referer
    referer_parts = urlsplit(default_referer)
    session.headers["Origin"] = f"{referer_parts.scheme}://{referer_parts.netloc}"
    current = url
    response = None
    try:
        for position in range(max_redirects + 1):
            response = session.get(
                current,
                timeout=timeout,
                allow_redirects=False,
                verify=certifi.where(),
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("Location")
            if not location:
                break
            if position >= max_redirects:
                raise ValueError("Exchange recovery redirect limit exceeded")
            next_url = urljoin(current, location)
            refuse_blocked_terms_target(next_url)
            validate_exchange_dataset_url(next_url, exchange)
            current = next_url
        if response is None:
            raise RuntimeError("Exchange recovery request produced no response")
        return FetchResponse(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get("Content-Type"),
            encoding=response.encoding,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            auth_mode="public",
            fetch_status=_classify_exchange_response(response),
        )
    finally:
        if owned:
            session.close()


