"""Bounded HTTP download and byte-signature validation for D02.

The URL suffix is deliberately never consulted. A response is eligible for
artifact content only when its status, declared media type, byte signature, and
logical artifact type agree.
"""

from __future__ import annotations

import io
import hashlib
import os
import tempfile
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import requests

from ..core.access_policy import refuse_blocked_terms_target
from ..config import (
    DOCUMENT_CONNECT_TIMEOUT_SECONDS,
    DOCUMENT_DOWNLOAD_CHUNK_BYTES,
    DOCUMENT_MAX_REDIRECTS,
    DOCUMENT_MAX_RESPONSE_BYTES,
    DOCUMENT_READ_TIMEOUT_SECONDS,
    DOCUMENT_TOTAL_TIMEOUT_SECONDS,
    USER_AGENT,
)


PDF = "application/pdf"
PPT = "application/vnd.ms-powerpoint"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

PRESENTATION_MEDIA_TYPES = frozenset({PDF, PPT, PPTX})
AUDIO_VIDEO_MEDIA_TYPES = frozenset(
    {
        "audio/flac",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "video/mp4",
        "video/mpeg",
        "video/quicktime",
        "video/webm",
        "video/x-msvideo",
    }
)

ALLOWED_MEDIA_BY_ARTIFACT = {
    "presentation": PRESENTATION_MEDIA_TYPES,
    "credit_rating": frozenset({PDF}),
    "transcript": frozenset({PDF}),
    "recording": AUDIO_VIDEO_MEDIA_TYPES,
}

GENERIC_DECLARED_MEDIA_TYPES = frozenset(
    {
        "",
        "application/download",
        "application/force-download",
        "application/octet-stream",
        "binary/octet-stream",
    }
)

DECLARED_MEDIA_ALIASES = {
    "application/x-pdf": PDF,
    "application/acrobat": PDF,
    "application/mspowerpoint": PPT,
    "application/powerpoint": PPT,
    "application/x-mspowerpoint": PPT,
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-flac": "audio/flac",
    "application/ogg": "audio/ogg",
    "application/mp4": "video/mp4",
    "video/x-m4v": "video/mp4",
}

STORED_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "content-disposition",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
    }
)
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class DownloadPolicy:
    max_bytes: int = DOCUMENT_MAX_RESPONSE_BYTES
    total_timeout_seconds: float = DOCUMENT_TOTAL_TIMEOUT_SECONDS
    connect_timeout_seconds: float = DOCUMENT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DOCUMENT_READ_TIMEOUT_SECONDS
    max_redirects: int = DOCUMENT_MAX_REDIRECTS
    chunk_bytes: int = DOCUMENT_DOWNLOAD_CHUNK_BYTES

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("connect/read timeouts must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        if self.chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "max_bytes": self.max_bytes,
            "total_timeout_seconds": self.total_timeout_seconds,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "read_timeout_seconds": self.read_timeout_seconds,
            "max_redirects": self.max_redirects,
            "chunk_bytes": self.chunk_bytes,
        }


@dataclass(frozen=True)
class RedirectHop:
    status_code: int
    url: str
    location: str | None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "status_code": self.status_code,
            "url": self.url,
            "location": self.location,
        }


@dataclass(frozen=True)
class DownloadResult:
    requested_url: str
    final_url: str | None
    http_status: int | None
    response_headers: dict[str, str]
    redirects: tuple[RedirectHop, ...]
    declared_media_type: str | None
    detected_media_type: str | None
    body: bytes | None
    byte_size: int | None
    result_status: str
    elapsed_ms: int
    error_code: str | None = None
    error_message: str | None = None
    stored_path: str | None = None
    content_sha256: str | None = None

    @property
    def is_validated(self) -> bool:
        return self.result_status == "validated"


def _recording_stream_destination(
    snapshot_root: Path, digest: str, media_type: str
) -> Path:
    suffixes = {
        "audio/flac": ".flac",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "video/mp4": ".mp4",
        "video/mpeg": ".mpeg",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "video/x-msvideo": ".avi",
    }
    return snapshot_root / digest[:2] / f"{digest}{suffixes[media_type]}"


def _hash_file(path: Path, *, chunk_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def cleanup_recording_temporary_files(
    snapshot_root: Path,
    *,
    older_than_seconds: int = 3600,
    now: Callable[[], float] = time.time,
) -> int:
    """Remove only stale, recognizable recording partials from the external store."""

    if older_than_seconds < 0:
        raise ValueError("older_than_seconds cannot be negative")
    temporary_root = snapshot_root / ".recording-tmp"
    if not temporary_root.is_dir():
        return 0
    removed = 0
    cutoff = now() - older_than_seconds
    for path in temporary_root.glob(".recording-stream-*.tmp"):
        try:
            if path.is_file() and path.stat().st_mtime <= cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def _declared_media_type(headers: Mapping[str, Any]) -> str | None:
    value = next(
        (str(v) for k, v in headers.items() if str(k).lower() == "content-type"),
        None,
    )
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None


def _safe_response_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if str(key).lower() in STORED_RESPONSE_HEADERS
    }


def _looks_like_html(content: bytes) -> bool:
    sample = content[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return sample.startswith(
        (
            b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml",
            b"<title", b"<meta", b"<link", b"<style", b"<div",
        )
    ) or b"<html" in sample[:1024]


def _is_pptx(content: bytes) -> bool:
    if not content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    return "[Content_Types].xml" in names and "ppt/presentation.xml" in names


def _is_legacy_ppt(content: bytes) -> bool:
    if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return False
    # CFB is shared by old Word/Excel/PowerPoint files. Requiring PowerPoint's
    # directory stream names prevents a generic OLE file from being accepted.
    return (
        "PowerPoint Document".encode("utf-16le") in content
        and "Current User".encode("utf-16le") in content
    )


def detect_media_type(content: bytes, declared_media_type: str | None = None) -> str | None:
    """Return a canonical media type from the bytes, never the URL extension."""

    if not content or _looks_like_html(content):
        return "text/html" if content and _looks_like_html(content) else None
    if content.find(b"%PDF-", 0, 1024) >= 0:
        return PDF
    if _is_pptx(content):
        return PPTX
    if _is_legacy_ppt(content):
        return PPT
    if content.startswith(b"fLaC"):
        return "audio/flac"
    if content.startswith(b"OggS"):
        return "audio/ogg"
    if content.startswith(b"RIFF") and len(content) >= 12:
        if content[8:12] == b"WAVE":
            return "audio/wav"
        if content[8:12] == b"AVI ":
            return "video/x-msvideo"
    if content.startswith(b"ID3") or (
        len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    if content.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if content.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
        return "video/mpeg"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in {b"M4A ", b"M4B ", b"F4A ", b"F4B "}:
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        if declared_media_type and declared_media_type.startswith("audio/"):
            return "audio/mp4"
        return "video/mp4"
    return None


def _declared_type_matches(declared: str | None, detected: str) -> bool:
    declared_key = declared or ""
    if declared_key in GENERIC_DECLARED_MEDIA_TYPES:
        return True
    canonical = DECLARED_MEDIA_ALIASES.get(declared_key, declared_key)
    if canonical == detected:
        return True
    # WebM and Ogg are sometimes served with the correct container but the
    # opposite audio/video top-level type. Both remain recording media.
    return {canonical, detected} in (
        {"audio/webm", "video/webm"},
        {"audio/ogg", "video/ogg"},
    )


def _elapsed_ms(started: float, monotonic: Callable[[], float]) -> int:
    return max(0, int(round((monotonic() - started) * 1000)))


class UnsafeRedirectError(ValueError):
    """Raised before following a malformed or unsafe redirect target."""


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
                raise requests.TooManyRedirects(
                    f"Exceeded {policy.max_redirects} validated redirects"
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


def download_recording_to_store(
    requested_url: str,
    *,
    snapshot_root: Path,
    policy: DownloadPolicy | None = None,
    session: requests.Session | Any | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    method: str = "GET",
    data: Mapping[str, str] | None = None,
    referer: str | None = None,
    blocked_terms_hosts: Iterable[str] = (),
) -> DownloadResult:
    """Stream one recording directly to the authorized external content store."""

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
    temporary: Path | None = None
    redirect_chain: list[RedirectHop] = []
    try:
        headers_out = {
            "User-Agent": USER_AGENT,
            "Accept": "audio/*,video/*,application/octet-stream;q=0.8",
        }
        if referer:
            headers_out["Referer"] = referer
        current_url = requested_url
        current_method = method.upper()
        current_data = dict(data or {})
        while True:
            refuse_blocked_terms_target(
                current_url, additional_hosts=blocked_terms_hosts
            )
            request = session.get if current_method == "GET" else session.post
            kwargs: dict[str, Any] = {
                "headers": headers_out,
                "stream": True,
                "allow_redirects": False,
                "timeout": (
                    policy.connect_timeout_seconds,
                    policy.read_timeout_seconds,
                ),
                "verify": certifi.where(),
            }
            if current_method == "POST":
                kwargs["data"] = current_data
            response = request(current_url, **kwargs)
            status_code = int(response.status_code)
            location = response.headers.get("Location")
            if status_code not in REDIRECT_STATUS_CODES or not location:
                break
            redirect_chain.append(
                RedirectHop(status_code, str(response.url), str(location))
            )
            if len(redirect_chain) > policy.max_redirects:
                raise requests.TooManyRedirects(
                    f"Exceeded {policy.max_redirects} validated redirects"
                )
            current_url = _validated_redirect_target(
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

        safe_headers = _safe_response_headers(response.headers)
        declared = _declared_media_type(response.headers)
        final_url = str(response.url)
        status_code = int(response.status_code)
        declared_length = None
        if safe_headers.get("content-length"):
            try:
                declared_length = int(safe_headers["content-length"])
            except ValueError:
                pass
        if declared_length is not None and declared_length > policy.max_bytes:
            return DownloadResult(
                requested_url, final_url, status_code, safe_headers,
                tuple(redirect_chain), declared, None, None, declared_length,
                "too_large", _elapsed_ms(started, monotonic),
                "content_length_exceeded",
                f"Content-Length {declared_length} exceeds {policy.max_bytes}",
            )

        temporary_root = snapshot_root / ".recording-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        file_handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".recording-stream-",
            suffix=".tmp",
            dir=temporary_root,
            delete=False,
        )
        temporary = Path(file_handle.name)
        digest = hashlib.sha256()
        sample = bytearray()
        received = 0
        with file_handle:
            for chunk in response.iter_content(chunk_size=policy.chunk_bytes):
                if not chunk:
                    continue
                if received + len(chunk) > policy.max_bytes:
                    return DownloadResult(
                        requested_url, final_url, status_code, safe_headers,
                        tuple(redirect_chain), declared,
                        detect_media_type(bytes(sample), declared), None,
                        received, "too_large", _elapsed_ms(started, monotonic),
                        "stream_size_exceeded",
                        f"Response exceeded {policy.max_bytes} bytes while streaming",
                    )
                file_handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if len(sample) < 65536:
                    sample.extend(chunk[: 65536 - len(sample)])
                if monotonic() - started > policy.total_timeout_seconds:
                    return DownloadResult(
                        requested_url, final_url, status_code, safe_headers,
                        tuple(redirect_chain), declared,
                        detect_media_type(bytes(sample), declared), None,
                        received, "timed_out", _elapsed_ms(started, monotonic),
                        "total_timeout_exceeded", "Total download deadline exceeded",
                    )
            file_handle.flush()
            os.fsync(file_handle.fileno())

        detected = detect_media_type(bytes(sample), declared)
        if status_code != 200:
            result_status, error_code = "rejected_http_status", f"http_{status_code}"
        elif detected == "text/html":
            result_status, error_code = "rejected_unexpected_html", "html_response"
        elif detected is None:
            result_status, error_code = "rejected_unknown_signature", "unknown_signature"
        elif not _declared_type_matches(declared, detected):
            result_status = "rejected_declared_media_type"
            error_code = "declared_media_type_mismatch"
        elif detected not in AUDIO_VIDEO_MEDIA_TYPES:
            result_status, error_code = "rejected_artifact_type", "artifact_type_mismatch"
        else:
            result_status, error_code = "validated", None
        if result_status != "validated":
            return DownloadResult(
                requested_url, final_url, status_code, safe_headers,
                tuple(redirect_chain), declared, detected, None, received,
                result_status, _elapsed_ms(started, monotonic), error_code, None,
            )

        content_sha256 = digest.hexdigest()
        destination = _recording_stream_destination(
            snapshot_root, content_sha256, str(detected)
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing_sha, existing_size = _hash_file(
                destination, chunk_bytes=policy.chunk_bytes
            )
            if existing_sha != content_sha256 or existing_size != received:
                raise RuntimeError(
                    f"Immutable recording snapshot mismatch: {destination}"
                )
        else:
            os.replace(temporary, destination)
            temporary = None
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            temporary = None
        return DownloadResult(
            requested_url, final_url, status_code, safe_headers,
            tuple(redirect_chain), declared, detected, None, received,
            "validated", _elapsed_ms(started, monotonic), None, None,
            str(destination.resolve()), content_sha256,
        )
    except requests.Timeout as exc:
        return DownloadResult(
            requested_url, None, None, {}, tuple(redirect_chain), None, None,
            None, None, "timed_out", _elapsed_ms(started, monotonic),
            "requests_timeout", str(exc) or type(exc).__name__,
        )
    except UnsafeRedirectError as exc:
        return DownloadResult(
            requested_url, None, None, {}, tuple(redirect_chain), None, None,
            None, None, "network_error", _elapsed_ms(started, monotonic),
            "unsafe_redirect", str(exc),
        )
    except requests.RequestException as exc:
        return DownloadResult(
            requested_url, None, None, {}, tuple(redirect_chain), None, None,
            None, None, "network_error", _elapsed_ms(started, monotonic),
            type(exc).__name__, str(exc),
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if response is not None:
            response.close()
        if owned_session:
            session.close()
