"""Streaming recording transport and atomic immutable storage."""
from __future__ import annotations
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from collections.abc import Iterable
import certifi
import requests
from ...config import USER_AGENT
from ...core.access_policy import refuse_blocked_terms_target
from .contracts import DownloadPolicy, DownloadResult, RedirectHop, UnsafeRedirectError
from .media import AUDIO_VIDEO_MEDIA_TYPES, REDIRECT_STATUS_CODES, detect_media_type, _declared_type_matches, _declared_media_type, _safe_response_headers
from .transport import _validated_redirect_target, _elapsed_ms

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
    AUDIO_VIDEO_MEDIA_TYPES=AUDIO_VIDEO_MEDIA_TYPES,
    REDIRECT_STATUS_CODES=REDIRECT_STATUS_CODES,
    _declared_media_type=_declared_media_type,
    _declared_type_matches=_declared_type_matches,
    _elapsed_ms=_elapsed_ms,
    _hash_file=_hash_file,
    _recording_stream_destination=_recording_stream_destination,
    _safe_response_headers=_safe_response_headers,
    _validated_redirect_target=_validated_redirect_target,
    detect_media_type=detect_media_type,
    refuse_blocked_terms_target=refuse_blocked_terms_target,
    USER_AGENT=USER_AGENT,
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
