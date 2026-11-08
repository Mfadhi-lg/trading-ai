"""Small HTTP client that returns raw response bytes and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlsplit

import certifi
import requests

from ..config import (
    REQUEST_TIMEOUT,
    SCREENER_COOKIE_FILE,
    SCREENER_THROTTLE_JITTER,
    SCREENER_THROTTLE_SECONDS,
    USER_AGENT,
)
from .access_policy import refuse_blocked_terms_target


@dataclass(frozen=True)
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str | None
    encoding: str | None
    etag: str | None
    last_modified: str | None
    auth_mode: str
    fetch_status: str


def load_screener_cookies(path: Path = SCREENER_COOKIE_FILE) -> dict[str, str]:
    """Read cookies without logging their values."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    cookies: dict[str, str] = {}
    if "=" not in raw:
        return {"sessionid": raw} if raw else {}
    for part in raw.replace("\n", ";").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def build_session(cookies: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    for key, value in (cookies or {}).items():
        session.cookies.set(key, value, domain=".screener.in")
    return session


def _classify_response(response: requests.Response) -> str:
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    final = urlsplit(response.url)
    if final.hostname != "www.screener.in" or final.path.startswith(("/login", "/register")):
        return "rejected"
    sample = response.content[:100_000].decode(response.encoding or "utf-8", errors="ignore").lower()
    if "captcha" in sample or "log in to screener" in sample or "access denied" in sample:
        return "rejected"
    if "id=\"company-info\"" not in sample and "id='company-info'" not in sample:
        return "rejected"
    return "ok"


def _classify_peer_response(response: requests.Response) -> str:
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    final = urlsplit(response.url)
    if final.hostname != "www.screener.in" or final.path.startswith(("/login", "/register")):
        return "rejected"
    sample = response.content[:100_000].decode(
        response.encoding or "utf-8", errors="ignore"
    ).lower()
    if "captcha" in sample or "log in to screener" in sample or "access denied" in sample:
        return "rejected"
    if "data-page-results" not in sample or "<table" not in sample:
        return "rejected"
    if "loading peers table" in sample:
        return "rejected"
    return "ok"


def _classify_quick_ratios_response(response: requests.Response) -> str:
    """Classify the AJAX quick-ratios HTML fragment."""
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    final = urlsplit(response.url)
    if final.hostname != "www.screener.in" or final.path.startswith(("/login", "/register")):
        return "rejected"
    sample = response.content[:100_000].decode(
        response.encoding or "utf-8", errors="ignore"
    ).lower()
    if "captcha" in sample or "log in to screener" in sample or "access denied" in sample:
        return "rejected"
    # An empty body (no custom ratios configured) is a valid "ok" with zero items.
    return "ok"


def _classify_commentary_response(response: requests.Response) -> str:
    """Classify the wiki/commentary modal HTML fragment."""
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    final = urlsplit(response.url)
    if final.hostname != "www.screener.in" or final.path.startswith(("/login", "/register")):
        return "rejected"
    sample = response.content[:200_000].decode(
        response.encoding or "utf-8", errors="ignore"
    ).lower()
    if "captcha" in sample or "log in to screener" in sample or "access denied" in sample:
        return "rejected"
    return "ok"


def validate_screener_result_screen_url(url: str) -> str:
    """Fail closed around the sole currently permitted discovery URL shape."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.screener.in"
        or parts.port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.path != "/screen/raw/"
        or parts.fragment
    ):
        raise ValueError("Result discovery URL must be https://www.screener.in/screen/raw/")
    parameters = parse_qsl(parts.query, keep_blank_values=True)
    if len(parameters) != 1 or parameters[0][0] != "query" or not parameters[0][1].strip():
        raise ValueError("Result discovery URL permits exactly one non-empty query parameter")
    return url


def _classify_result_screen_response(response: requests.Response) -> str:
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    final = urlsplit(response.url)
    if final.hostname != "www.screener.in" or final.path.startswith(("/login", "/register")):
        return "rejected"
    sample = response.content[:200_000].decode(
        response.encoding or "utf-8", errors="ignore"
    ).lower()
    blocked = ("captcha", "access denied", "log in to screener", "login to screener")
    if any(marker in sample for marker in blocked):
        return "rejected"
    if "data-page-info" not in sample or "data-page-results" not in sample or "<table" not in sample:
        return "rejected"
    return "ok"


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


def validate_exchange_dataset_url(url: str, exchange: str) -> str:
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


def _classify_exchange_response(response: requests.Response) -> str:
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


def _classify_trendlyne_response(response: requests.Response) -> str:
    if response.status_code >= 400:
        return "rejected" if response.status_code in {401, 403, 429} else "error"
    sample = response.content[:200_000].decode(
        response.encoding or "utf-8", errors="ignore"
    ).lower()
    blocked_markers = ("captcha", "access denied", "sign in to trendlyne")
    if any(marker in sample for marker in blocked_markers):
        return "rejected"
    required_markers = (
        "monthly-mutual-fund-share-holding",
        "stock_info_card",
        "choose month",
    )
    if not all(marker in sample for marker in required_markers):
        return "rejected"
    return "ok"


def fetch_screener(url: str, cookie_path: Path = SCREENER_COOKIE_FILE) -> FetchResponse:
    cookies = load_screener_cookies(cookie_path)
    session = build_session(cookies)
    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        auth_mode="authenticated" if cookies else "public",
        fetch_status=_classify_response(response),
    )


def fetch_screener_peers(
    url: str,
    referer: str,
    cookie_path: Path = SCREENER_COOKIE_FILE,
) -> FetchResponse:
    """Fetch the first-party XHR fragment that supplies the peer table."""
    cookies = load_screener_cookies(cookie_path)
    session = build_session(cookies)
    response = session.get(
        url,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Accept": "text/html",
        },
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        auth_mode="authenticated" if cookies else "public",
        fetch_status=_classify_peer_response(response),
    )


def fetch_screener_quick_ratios(
    url: str,
    referer: str,
    cookie_path: Path = SCREENER_COOKIE_FILE,
) -> FetchResponse:
    """Fetch the AJAX quick-ratios HTML fragment (custom user ratios)."""
    cookies = load_screener_cookies(cookie_path)
    session = build_session(cookies)
    response = session.get(
        url,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Accept": "text/html",
        },
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        auth_mode="authenticated" if cookies else "public",
        fetch_status=_classify_quick_ratios_response(response),
    )


def fetch_screener_commentary(
    url: str,
    referer: str,
    cookie_path: Path = SCREENER_COOKIE_FILE,
) -> FetchResponse:
    """Fetch the wiki/commentary modal fragment (Key Insights)."""
    cookies = load_screener_cookies(cookie_path)
    session = build_session(cookies)
    response = session.get(
        url,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Accept": "text/html",
        },
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        auth_mode="authenticated" if cookies else "public",
        fetch_status=_classify_commentary_response(response),
    )


def fetch_screener_result_screen(
    url: str,
    cookie_path: Path = SCREENER_COOKIE_FILE,
) -> FetchResponse:
    """Fetch one authenticated, query-only result page.

    Pagination, sorting and limit parameters are intentionally rejected here.
    Redirects are followed only so a login/register destination can be classified
    as rejected; no credential material is placed in returned provenance.
    """
    validate_screener_result_screen_url(url)
    cookies = load_screener_cookies(cookie_path)
    if not cookies.get("sessionid"):
        raise ValueError("An available Screener session cookie is required for discovery")
    session = build_session(cookies)
    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        auth_mode="authenticated",
        fetch_status=_classify_result_screen_response(response),
    )


class ScreenerHostClient:
    """One authenticated session and one rate limiter for a worker process."""

    def __init__(
        self,
        cookie_path: Path = SCREENER_COOKIE_FILE,
        *,
        base_delay: float = SCREENER_THROTTLE_SECONDS,
        jitter: float = SCREENER_THROTTLE_JITTER,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        cookies = load_screener_cookies(cookie_path)
        if not cookies.get("sessionid"):
            raise ValueError("An available Screener session cookie is required for company workers")
        if base_delay < 0 or jitter < 0:
            raise ValueError("Screener throttle values cannot be negative")
        self.session = build_session(cookies)
        self.base_delay = base_delay
        self.jitter = jitter
        self._sleep = sleep
        self._monotonic = monotonic
        self._random = random_value
        self._last_request_at: float | None = None

    def _get(self, url: str, *, headers: dict[str, str] | None = None) -> requests.Response:
        if self._last_request_at is not None:
            minimum = self.base_delay + self.jitter * self._random()
            remaining = minimum - (self._monotonic() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        response = self.session.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            verify=certifi.where(),
        )
        self._last_request_at = self._monotonic()
        return response

    @staticmethod
    def _response(url: str, response: requests.Response, status: str) -> FetchResponse:
        return FetchResponse(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get("Content-Type"),
            encoding=response.encoding,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            auth_mode="authenticated",
            fetch_status=status,
        )

    def fetch_company(self, url: str) -> FetchResponse:
        response = self._get(url)
        return self._response(url, response, _classify_response(response))

    def fetch_peers(self, url: str, referer: str) -> FetchResponse:
        response = self._get(
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
                "Accept": "text/html",
            },
        )
        return self._response(url, response, _classify_peer_response(response))

    def fetch_quick_ratios(self, url: str, referer: str) -> FetchResponse:
        """Fetch the AJAX quick-ratios fragment (custom user ratios)."""
        response = self._get(
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
                "Accept": "text/html",
            },
        )
        return self._response(url, response, _classify_quick_ratios_response(response))

    def fetch_commentary(self, url: str, referer: str) -> FetchResponse:
        """Fetch the wiki/commentary modal fragment (Key Insights)."""
        response = self._get(
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": referer,
                "Accept": "text/html",
            },
        )
        return self._response(url, response, _classify_commentary_response(response))


def fetch_trendlyne(url: str) -> FetchResponse:
    """Fetch one public Trendlyne HTML page without carrying Screener cookies."""
    session = build_session()
    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        verify=certifi.where(),
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
        fetch_status=_classify_trendlyne_response(response),
    )
