"""Persistence for append-only D02 document/media download attempts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from ..config import DOCUMENT_CONTENT_SNAPSHOTS
from ..documents.downloader import DownloadPolicy, DownloadResult
from .document_artifact_repo import register_artifact_content


from .documents.download import MEDIA_SUFFIXES


from .documents.download import PersistedDownload


def _store_response_blob(
    conn: sqlite3.Connection,
    *,
    body: bytes,
    media_type: str | None,
    observed_at_utc: str,
    snapshot_root: Path,
) -> tuple[int, str]:
    """Compatible entry point for the extracted owner."""
    from .documents.download import _store_response_blob as _owner
    return _owner(
        conn,
        body=body,
        media_type=media_type,
        observed_at_utc=observed_at_utc,
        snapshot_root=snapshot_root,
        MEDIA_SUFFIXES=MEDIA_SUFFIXES,
    )


def _store_streamed_response_blob(
    conn: sqlite3.Connection,
    *,
    path: Path,
    content_sha256: str,
    byte_size: int,
    media_type: str,
    observed_at_utc: str,
    snapshot_root: Path,
) -> tuple[int, str]:
    """Compatible entry point for the extracted owner."""
    from .documents.download import _store_streamed_response_blob as _owner
    return _owner(
        conn,
        path=path,
        content_sha256=content_sha256,
        byte_size=byte_size,
        media_type=media_type,
        observed_at_utc=observed_at_utc,
        snapshot_root=snapshot_root,
    )


def store_response_blob(
    conn: sqlite3.Connection,
    *,
    body: bytes,
    media_type: str | None,
    observed_at_utc: str,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
) -> tuple[int, str]:
    """Compatible entry point for the extracted owner."""
    from .documents.download import store_response_blob as _owner
    return _owner(
        conn,
        body=body,
        media_type=media_type,
        observed_at_utc=observed_at_utc,
        snapshot_root=snapshot_root,
        _store_response_blob=_store_response_blob,
    )


def _validated_sources(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    artifact_source_ids: Sequence[int],
    requested_url: str,
) -> tuple[sqlite3.Row, ...]:
    """Compatible entry point for the extracted owner."""
    from .documents.download import _validated_sources as _owner
    return _owner(
        conn,
        artifact_id=artifact_id,
        artifact_source_ids=artifact_source_ids,
        requested_url=requested_url,
    )


def _is_official_retrieval(source: sqlite3.Row) -> bool:
    """Compatible entry point for the extracted owner."""
    from .documents.download import _is_official_retrieval as _owner
    return _owner(source)


def persist_download_result(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    artifact_source_ids: Sequence[int],
    result: DownloadResult,
    policy: DownloadPolicy,
    started_at_utc: str,
    fetched_at_utc: str,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    content_origin: str = "publisher_original",
    official_override: bool | None = None,
) -> PersistedDownload:
    """Compatible entry point for the extracted owner."""
    from .documents.download import persist_download_result as _owner
    return _owner(
        conn,
        artifact_id=artifact_id,
        artifact_source_ids=artifact_source_ids,
        result=result,
        policy=policy,
        started_at_utc=started_at_utc,
        fetched_at_utc=fetched_at_utc,
        snapshot_root=snapshot_root,
        content_origin=content_origin,
        official_override=official_override,
        _is_official_retrieval=_is_official_retrieval,
        _store_response_blob=_store_response_blob,
        _store_streamed_response_blob=_store_streamed_response_blob,
        _validated_sources=_validated_sources,
        register_artifact_content=register_artifact_content,
    )
