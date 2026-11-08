"""Bounded HTTP transport; no queue or content persistence ownership."""
from __future__ import annotations
import time
from collections.abc import Iterable
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit
import certifi
import requests
from .tls import install_verified_chain
from ...core.access_policy import refuse_blocked_terms_target
from ...config import USER_AGENT
from .contracts import DownloadPolicy, RedirectHop, DownloadResult, UnsafeRedirectError
from .media import ALLOWED_MEDIA_BY_ARTIFACT, REDIRECT_STATUS_CODES, detect_media_type, _declared_media_type, _safe_response_headers, _declared_type_matches

def _elapsed_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, int(round((monotonic() - started) * 1000)))


def _validated_redirect_target(
    current_url: str,
    location: str,
    *,
    blocked_terms_hosts: Iterable[str],
) -> str:
    """Resolve and validate one redirect before another request is dispatched."""

    target = urlsplit(urljoin(current_url, location.strip()))
    try:
        port = target.port
    except ValueError as exc:
        raise UnsafeRedirectError("Redirect target has an invalid port") from exc
    if (
        target.scheme.lower() not in {"http", "https"}
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise UnsafeRedirectError(
            "Redirect target must be an absolute HTTP(S) URL without credentials"
        )
    normalized = urlunsplit(
        (target.scheme.lower(), target.netloc, target.path, target.query, "")
    )
    refuse_blocked_terms_target(
        normalized, additional_hosts=blocked_terms_hosts
    )
    return normalized


def download_url(
    requested_url: str,
    *,
    artifact_type: str,
    policy: DownloadPolicy | None = None,
    session: requests.Session | Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    method: str = "GET",
    data: Mapping[str, str] | None = None,
    referer: str | None = None,
    blocked_terms_hosts: Iterable[str] = (),
    detect_media_type=detect_media_type,
    _declared_media_type=_declared_media_type,
    _safe_response_headers=_safe_response_headers,
    _declared_type_matches=_declared_type_matches,
    _validated_redirect_target=_validated_redirect_target,
    _elapsed_ms=_elapsed_ms,
    refuse_blocked_terms_target=refuse_blocked_terms_target,
) -> DownloadResult:
    """Download one URL with streaming byte/time bounds and fail-closed checks."""

    if artifact_type not in ALLOWED_MEDIA_BY_ARTIFACT:
        raise ValueError(f"Unsupported artifact type: {artifact_type!r}")
    # This must remain before session construction and request dispatch. Queue
    # policy is defense in depth, not a prerequisite for HTTP safety.
    refuse_blocked_terms_target(
        requested_url, additional_hosts=blocked_terms_hosts
    )
    policy = policy or DownloadPolicy()
    owned_session = session is None
    session = session or requests.Session()
    if hasattr(session, "max_redirects"):
        session.max_redirects = policy.max_redirects
    started = monotonic()
    response = None
    try:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/pdf,application/vnd.ms-powerpoint,"
                "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
                "audio/*,video/*,application/octet-stream;q=0.8,text/html;q=0.7"
            ),
        }
        if referer:
            request_headers["Referer"] = referer
        current_url = requested_url
        current_method = method.upper()
        current_data = dict(data or {})
        redirect_chain: list[RedirectHop] = []
        while True:
            refuse_blocked_terms_target(
                current_url, additional_hosts=blocked_terms_hosts
            )
            request = session.get if current_method == "GET" else session.post
            request_kwargs: dict[str, Any] = {
                "headers": request_headers,
                "stream": True,
                # Redirects are followed manually so every hop crosses the
                # same policy boundary before any network dispatch.
                "allow_redirects": False,
                "timeout": (
                    policy.connect_timeout_seconds,
                    policy.read_timeout_seconds,
                ),
                "verify": certifi.where(),
            }
            if current_method == "POST":
                request_kwargs["data"] = current_data
            install_verified_chain(session, current_url)
            response = request(current_url, **request_kwargs)
            status_code = int(response.status_code)
            location = response.headers.get("Location")
            if status_code not in REDIRECT_STATUS_CODES or not location:
                break
            redirect_chain.append(
                RedirectHop(
                    status_code=status_code,
                    url=str(response.url),
                    location=str(location),
                )
            )
            if len(redirect_chain) > policy.max_redirects:
                return DownloadResult(
                    requested_url,str(response.url),status_code,
                    _safe_response_headers(response.headers),tuple(redirect_chain),
                    _declared_media_type(response.headers),None,None,None,
                    'network_error',_elapsed_ms(started,monotonic),
                    'redirect_limit_exceeded',f'Exceeded {policy.max_redirects} validated redirects',
                )
            next_url = _validated_redirect_target(
                str(response.url),
                str(location),
                blocked_terms_hosts=blocked_terms_hosts,
            )
            response.close()
            response = None
            if status_code == 303 or (
                status_code in {301, 302} and current_method == "POST"
            ):
                current_method = "GET"
                current_data = {}
            current_url = next_url
        headers = _safe_response_headers(response.headers)
        declared = _declared_media_type(response.headers)
        redirects = tuple(redirect_chain)
        final_url = str(response.url)
        status_code = int(response.status_code)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > policy.max_bytes:
                return DownloadResult(
                    requested_url, final_url, status_code, headers, redirects,
                    declared, None, None, declared_length, "too_large",
                    _elapsed_ms(started, monotonic), "content_length_exceeded",
                    f"Content-Length {declared_length} exceeds {policy.max_bytes}",
                )

        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_content(chunk_size=policy.chunk_bytes):
            if not chunk:
                continue
            remaining = policy.max_bytes - received
            if len(chunk) > remaining:
                if remaining:
                    chunks.append(bytes(chunk[:remaining]))
                    received += remaining
                partial = b"".join(chunks) or None
                return DownloadResult(
                    requested_url, final_url, status_code, headers, redirects,
                    declared,
                    detect_media_type(partial, declared) if partial else None,
                    partial, received, "too_large",
                    _elapsed_ms(started, monotonic), "stream_size_exceeded",
                    f"Response exceeded {policy.max_bytes} bytes while streaming",
                )
            chunks.append(bytes(chunk))
            received += len(chunk)
            if monotonic() - started > policy.total_timeout_seconds:
                partial = b"".join(chunks)
                return DownloadResult(
                    requested_url, final_url, status_code, headers, redirects,
                    declared, detect_media_type(partial, declared), partial,
                    len(partial), "timed_out", _elapsed_ms(started, monotonic),
                    "total_timeout_exceeded", "Total download deadline exceeded",
                )
        body = b"".join(chunks)
        detected = detect_media_type(body, declared)
        elapsed = _elapsed_ms(started, monotonic)
        if status_code != 200:
            result_status = "rejected_http_status"
            error_code = f"http_{status_code}"
        elif detected == "text/html":
            result_status = "rejected_unexpected_html"
            error_code = "html_response"
        elif detected is None:
            result_status = "rejected_unknown_signature"
            error_code = "unknown_signature"
        elif not _declared_type_matches(declared, detected):
            result_status = "rejected_declared_media_type"
            error_code = "declared_media_type_mismatch"
        elif detected not in ALLOWED_MEDIA_BY_ARTIFACT[artifact_type]:
            result_status = "rejected_artifact_type"
            error_code = "artifact_type_mismatch"
        else:
            result_status = "validated"
            error_code = None
        return DownloadResult(
            requested_url=requested_url,
            final_url=final_url,
            http_status=status_code,
            response_headers=headers,
            redirects=redirects,
            declared_media_type=declared,
            detected_media_type=detected,
            body=body,
            byte_size=len(body),
            result_status=result_status,
            elapsed_ms=elapsed,
            error_code=error_code,
            error_message=None,
        )
    except requests.Timeout as exc:
        return DownloadResult(
            requested_url, None, None, {}, (), None, None, None, None,
            "timed_out", _elapsed_ms(started, monotonic), "requests_timeout",
            str(exc) or type(exc).__name__,
        )
    except UnsafeRedirectError as exc:
        return DownloadResult(
            requested_url, None, None, {}, tuple(redirect_chain), None, None,
            None, None, "network_error", _elapsed_ms(started, monotonic),
            "unsafe_redirect", str(exc),
        )
    except requests.RequestException as exc:
        return DownloadResult(
            requested_url, None, None, {}, (), None, None, None, None,
            "network_error", _elapsed_ms(started, monotonic),
            type(exc).__name__, str(exc),
        )
    finally:
        if response is not None:
            response.close()
        if owned_session:
            session.close()
