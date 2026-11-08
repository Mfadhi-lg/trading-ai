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
    from ..sources.exchange import transport as _owner
    return _owner.validate_exchange_dataset_url(url, exchange, EXCHANGE_HOSTS=EXCHANGE_HOSTS, urlsplit=urlsplit)


def _classify_exchange_response(response: requests.Response) -> str:
    from ..sources.exchange import transport as _owner
    return _owner._classify_exchange_response(response, GZIP_MAGIC=GZIP_MAGIC, ZIP_EMPTY_MAGIC=ZIP_EMPTY_MAGIC, ZIP_MAGIC=ZIP_MAGIC, _EXCHANGE_MEDIA_TYPES=_EXCHANGE_MEDIA_TYPES)


def fetch_exchange_dataset(
    url: str,
    exchange: str,
    *,
    referer: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
) -> FetchResponse:
    from ..sources.exchange import transport as _owner
    return _owner.fetch_exchange_dataset(url, exchange, referer=referer, timeout=timeout, FetchResponse=FetchResponse, USER_AGENT=USER_AGENT, _EXCHANGE_REFERERS=_EXCHANGE_REFERERS, _classify_exchange_response=_classify_exchange_response, certifi=certifi, requests=requests, urlsplit=urlsplit, validate_exchange_dataset_url=validate_exchange_dataset_url)


def fetch_exchange_recovery_surface(
    url: str,
    exchange: str,
    *,
    referer: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
    max_redirects: int = 3,
    session: requests.Session | Any | None = None,
) -> FetchResponse:
    from ..sources.exchange import transport as _owner
    return _owner.fetch_exchange_recovery_surface(url, exchange, referer=referer, timeout=timeout, max_redirects=max_redirects, session=session, FetchResponse=FetchResponse, USER_AGENT=USER_AGENT, _EXCHANGE_REFERERS=_EXCHANGE_REFERERS, _classify_exchange_response=_classify_exchange_response, certifi=certifi, refuse_blocked_terms_target=refuse_blocked_terms_target, requests=requests, urljoin=urljoin, urlsplit=urlsplit, validate_exchange_dataset_url=validate_exchange_dataset_url)


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
