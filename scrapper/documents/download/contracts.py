"""Stable download policy, redirect and result value contracts."""
from __future__ import annotations
from dataclasses import dataclass
from ...config import (DOCUMENT_CONNECT_TIMEOUT_SECONDS, DOCUMENT_DOWNLOAD_CHUNK_BYTES, DOCUMENT_MAX_REDIRECTS, DOCUMENT_MAX_RESPONSE_BYTES, DOCUMENT_READ_TIMEOUT_SECONDS, DOCUMENT_TOTAL_TIMEOUT_SECONDS)

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


class UnsafeRedirectError(ValueError):
    """Raised before following a malformed or unsafe redirect target."""


# Preserve the legacy serialization/import identity while the facade remains.
for _contract in (DownloadPolicy, RedirectHop, DownloadResult, UnsafeRedirectError):
    _contract.__module__ = "scrapper.documents.downloader"
