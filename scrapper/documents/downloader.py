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


from .download.media import (
    PDF,
    PPT,
    PPTX,
    PRESENTATION_MEDIA_TYPES,
    AUDIO_VIDEO_MEDIA_TYPES,
    ALLOWED_MEDIA_BY_ARTIFACT,
    GENERIC_DECLARED_MEDIA_TYPES,
    DECLARED_MEDIA_ALIASES,
    STORED_RESPONSE_HEADERS,
    REDIRECT_STATUS_CODES,
    _declared_media_type,
    _safe_response_headers,
    _looks_like_html,
    _is_pptx,
    _is_legacy_ppt,
    detect_media_type,
    _declared_type_matches,
)
from .download.contracts import (
    DownloadPolicy,
    RedirectHop,
    DownloadResult,
    UnsafeRedirectError,
)
from .download.transport import (
    _elapsed_ms,
    _validated_redirect_target,
)


def _recording_stream_destination(
    snapshot_root: Path, digest: str, media_type: str
) -> Path:
    """Compatible entry point for the extracted owner."""
    from .download.recording import _recording_stream_destination as _owner
    return _owner(snapshot_root, digest, media_type)


def _hash_file(path: Path, *, chunk_bytes: int) -> tuple[str, int]:
    """Compatible entry point for the extracted owner."""
    from .download.recording import _hash_file as _owner
    return _owner(path, chunk_bytes=chunk_bytes)


def cleanup_recording_temporary_files(
    snapshot_root: Path,
    *,
    older_than_seconds: int = 3600,
    now: Callable[[], float] = time.time,
) -> int:
    """Compatible entry point for the extracted owner."""
    from .download.recording import cleanup_recording_temporary_files as _owner
    return _owner(snapshot_root, older_than_seconds=older_than_seconds, now=now)


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
    """Compatible entry point for the bounded transport owner."""
    from .download.transport import download_url as _owner
    return _owner(
        requested_url,
        artifact_type=artifact_type,
        policy=policy,
        session=session,
        monotonic=monotonic,
        method=method,
        data=data,
        referer=referer,
        blocked_terms_hosts=blocked_terms_hosts,
        detect_media_type=detect_media_type,
        _declared_media_type=_declared_media_type,
        _safe_response_headers=_safe_response_headers,
        _declared_type_matches=_declared_type_matches,
        _validated_redirect_target=_validated_redirect_target,
        _elapsed_ms=_elapsed_ms,
        refuse_blocked_terms_target=refuse_blocked_terms_target,
    )


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
    """Compatible entry point for the extracted owner."""
    from .download.recording import download_recording_to_store as _owner
    return _owner(
        requested_url,
        snapshot_root=snapshot_root,
        policy=policy,
        session=session,
        monotonic=monotonic,
        method=method,
        data=data,
        referer=referer,
        blocked_terms_hosts=blocked_terms_hosts,
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
    )
