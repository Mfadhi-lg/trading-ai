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

from ...config import DOCUMENT_CONTENT_SNAPSHOTS
from ...documents.download.contracts import DownloadPolicy, DownloadResult
from ..document_artifact_repo import register_artifact_content


MEDIA_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
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
    "text/html": ".html",
}


@dataclass(frozen=True)
class PersistedDownload:
    download_attempt_id: int
    artifact_id: int
    artifact_source_ids: tuple[int, ...]
    requested_url: str
    final_url: str | None
    result_status: str
    http_status: int | None
    declared_media_type: str | None
    detected_media_type: str | None
    byte_size: int | None
    content_sha256: str | None
    blob_id: int | None
    content_id: int | None
    redirect_count: int
    started_at_utc: str
    fetched_at_utc: str
    elapsed_ms: int
    response_headers: dict[str, str]
    redirect_chain: tuple[dict[str, Any], ...]
    source_provenance: tuple[dict[str, Any], ...]
    error: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_attempt_id": self.download_attempt_id,
            "artifact_id": self.artifact_id,
            "artifact_source_ids": list(self.artifact_source_ids),
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "result_status": self.result_status,
            "http_status": self.http_status,
            "declared_media_type": self.declared_media_type,
            "detected_media_type": self.detected_media_type,
            "byte_size": self.byte_size,
            "content_sha256": self.content_sha256,
            "blob_id": self.blob_id,
            "content_id": self.content_id,
            "redirect_count": self.redirect_count,
            "started_at_utc": self.started_at_utc,
            "fetched_at_utc": self.fetched_at_utc,
            "elapsed_ms": self.elapsed_ms,
            "response_headers": self.response_headers,
            "redirect_chain": list(self.redirect_chain),
            "source_provenance": list(self.source_provenance),
            "error": self.error,
        }

PersistedDownload.__module__ = 'scrapper.db.document_download_repo'


def _store_response_blob(
    conn: sqlite3.Connection,
    *,
    body: bytes,
    media_type: str | None,
    observed_at_utc: str,
    snapshot_root: Path,
    MEDIA_SUFFIXES=MEDIA_SUFFIXES,
) -> tuple[int, str]:
    digest = hashlib.sha256(body).hexdigest()
    existing = conn.execute(
        "SELECT blob_id, byte_size, local_path FROM response_blobs WHERE content_sha256=?",
        (digest,),
    ).fetchone()
    if existing is not None:
        if int(existing["byte_size"]) != len(body):
            raise RuntimeError("Existing response blob has a conflicting byte size")
        existing_path = Path(existing["local_path"])
        if not existing_path.exists():
            raise RuntimeError(f"Immutable response blob is missing: {existing_path}")
        if hashlib.sha256(existing_path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Immutable response blob hash mismatch: {existing_path}")
        return int(existing["blob_id"]), digest

    suffix = MEDIA_SUFFIXES.get(media_type or "", ".bin")
    destination = snapshot_root / digest[:2] / f"{digest}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Immutable document snapshot mismatch: {destination}")
    else:
        handle = tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{digest}.", suffix=".tmp",
            dir=destination.parent, delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    cursor = conn.execute(
        """INSERT INTO response_blobs
           (content_sha256, content_type, encoding, byte_size, local_path,
            first_observed_at_utc, created_at_utc)
           VALUES (?, ?, NULL, ?, ?, ?, ?)""",
        (
            digest,
            media_type,
            len(body),
            str(destination.resolve()),
            observed_at_utc,
            observed_at_utc,
        ),
    )
    return int(cursor.lastrowid), digest


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
    """Register bytes already atomically finalized on the external store."""

    resolved = path.resolve()
    root = snapshot_root.resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError("Streamed recording path is outside the document store")
    if not resolved.is_file() or resolved.stat().st_size != byte_size:
        raise RuntimeError("Durable streamed recording byte size mismatch")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != content_sha256:
        raise RuntimeError("Durable streamed recording hash mismatch")
    existing = conn.execute(
        "SELECT blob_id,byte_size,local_path FROM response_blobs WHERE content_sha256=?",
        (content_sha256,),
    ).fetchone()
    if existing is not None:
        if int(existing["byte_size"]) != byte_size:
            raise RuntimeError("Existing response blob has a conflicting byte size")
        existing_path = Path(str(existing["local_path"]))
        if existing_path.resolve() != resolved:
            raise RuntimeError("Content hash resolves to a different immutable path")
        return int(existing["blob_id"]), content_sha256
    cursor = conn.execute(
        """INSERT INTO response_blobs
           (content_sha256,content_type,encoding,byte_size,local_path,
            first_observed_at_utc,created_at_utc)
           VALUES (?,?,NULL,?,?,?,?)""",
        (
            content_sha256,
            media_type,
            byte_size,
            str(resolved),
            observed_at_utc,
            observed_at_utc,
        ),
    )
    return int(cursor.lastrowid), content_sha256


def store_response_blob(
    conn: sqlite3.Connection,
    *,
    body: bytes,
    media_type: str | None,
    observed_at_utc: str,
    snapshot_root: Path = DOCUMENT_CONTENT_SNAPSHOTS,
    _store_response_blob=_store_response_blob,
) -> tuple[int, str]:
    """Content-address generated or downloaded bytes using the D02 blob rules."""

    return _store_response_blob(
        conn,
        body=body,
        media_type=media_type,
        observed_at_utc=observed_at_utc,
        snapshot_root=snapshot_root,
    )


def _validated_sources(
    conn: sqlite3.Connection,
    *,
    artifact_id: int,
    artifact_source_ids: Sequence[int],
    requested_url: str,
) -> tuple[sqlite3.Row, ...]:
    source_ids = tuple(dict.fromkeys(int(item) for item in artifact_source_ids))
    if not source_ids:
        raise ValueError("At least one artifact source is required")
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""SELECT artifact_source_id, artifact_id, discovered_from_source,
                   discovery_page_url, document_url, is_primary_source,
                   document_originator, retrieval_source_type
            FROM artifact_sources
            WHERE artifact_source_id IN ({placeholders})
            ORDER BY artifact_source_id""",
        source_ids,
    ).fetchall()
    if len(rows) != len(source_ids):
        raise ValueError("One or more artifact sources do not exist")
    for row in rows:
        if int(row["artifact_id"]) != artifact_id:
            raise ValueError("Artifact source belongs to a different logical artifact")
        if row["document_url"] != requested_url:
            raise ValueError("Artifact source URL does not match the requested URL")
    return tuple(rows)


def _is_official_retrieval(source: sqlite3.Row) -> bool:
    if bool(source["is_primary_source"]):
        return True
    if source["document_originator"] and source["retrieval_source_type"]:
        return True
    host = (urlsplit(str(source["document_url"])).hostname or "").lower().rstrip(".")
    return host in {"bseindia.com", "nseindia.com"} or host.endswith(
        (".bseindia.com", ".nseindia.com")
    )


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
    _is_official_retrieval=_is_official_retrieval,
    _store_response_blob=_store_response_blob,
    _store_streamed_response_blob=_store_streamed_response_blob,
    _validated_sources=_validated_sources,
    register_artifact_content=register_artifact_content,
) -> PersistedDownload:
    """Persist one actual request and link every discovery path it represents."""

    sources = _validated_sources(
        conn,
        artifact_id=artifact_id,
        artifact_source_ids=artifact_source_ids,
        requested_url=result.requested_url,
    )
    body = result.body
    blob_id: int | None = None
    content_id: int | None = None
    digest: str | None = None
    with conn:
        if body is not None:
            blob_id, digest = _store_response_blob(
                conn,
                body=body,
                media_type=result.detected_media_type or result.declared_media_type,
                observed_at_utc=fetched_at_utc,
                snapshot_root=snapshot_root,
            )
        elif result.stored_path is not None or result.content_sha256 is not None:
            if (
                not result.is_validated
                or result.stored_path is None
                or result.content_sha256 is None
                or result.byte_size is None
                or result.detected_media_type is None
            ):
                raise ValueError("Only validated durable streams may be registered")
            blob_id, digest = _store_streamed_response_blob(
                conn,
                path=Path(result.stored_path),
                content_sha256=result.content_sha256,
                byte_size=result.byte_size,
                media_type=result.detected_media_type,
                observed_at_utc=fetched_at_utc,
                snapshot_root=snapshot_root,
            )
        if result.is_validated:
            if blob_id is None or digest is None or result.detected_media_type is None:
                raise ValueError("Validated downloads must contain typed immutable bytes")
            content_id = register_artifact_content(
                conn,
                artifact_id=artifact_id,
                blob_id=blob_id,
                media_type=result.detected_media_type,
                is_official=(official_override if official_override is not None
                             else any(_is_official_retrieval(row) for row in sources)),
                content_origin=content_origin,
                created_at_utc=fetched_at_utc,
            )
        error_json = json.dumps(
            {
                "code": result.error_code,
                "message": result.error_message,
            }
            if result.error_code or result.error_message
            else {},
            sort_keys=True,
            separators=(",", ":"),
        )
        cursor = conn.execute(
            """INSERT INTO artifact_download_attempts
               (artifact_id, requested_url, final_url, started_at_utc,
                fetched_at_utc, elapsed_ms, http_status, response_headers_json,
                redirect_chain_json, declared_media_type, detected_media_type,
                byte_size, content_sha256, blob_id, content_id, result_status,
                limits_json, error_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                result.requested_url,
                result.final_url,
                started_at_utc,
                fetched_at_utc,
                result.elapsed_ms,
                result.http_status,
                json.dumps(result.response_headers, sort_keys=True, separators=(",", ":")),
                json.dumps(
                    [item.to_dict() for item in result.redirects],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                result.declared_media_type,
                result.detected_media_type,
                result.byte_size,
                digest,
                blob_id,
                content_id,
                result.result_status,
                json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":")),
                error_json,
            ),
        )
        attempt_id = int(cursor.lastrowid)
        conn.executemany(
            """INSERT INTO artifact_download_attempt_sources
               (download_attempt_id, artifact_source_id) VALUES (?, ?)""",
            [(attempt_id, int(row["artifact_source_id"])) for row in sources],
        )
        inaccessible_result = (
            result.result_status == "rejected_unexpected_html"
            or result.result_status == "rejected_http_status"
            and result.http_status in {401, 403, 404}
        )
        source_status = (
            "available"
            if result.is_validated
            else "inaccessible"
            if inaccessible_result
            else None
        )
        if source_status is None:
            conn.executemany(
                "UPDATE artifact_sources SET last_checked_at_utc=? WHERE artifact_source_id=?",
                [(fetched_at_utc, int(row["artifact_source_id"])) for row in sources],
            )
        else:
            conn.executemany(
                """UPDATE artifact_sources
                   SET last_checked_at_utc=?, availability_status=?
                   WHERE artifact_source_id=?""",
                [
                    (fetched_at_utc, source_status, int(row["artifact_source_id"]))
                    for row in sources
                ],
            )
        if result.is_validated:
            conn.execute(
                """UPDATE document_artifacts SET acquisition_status='downloaded'
                   WHERE artifact_id=? AND acquisition_status <> 'validated'""",
                (artifact_id,),
            )
        else:
            existing_content = conn.execute(
                "SELECT 1 FROM artifact_contents WHERE artifact_id=? LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if existing_content is None:
                status = (
                    "inaccessible"
                    if inaccessible_result
                    else "failed"
                )
                conn.execute(
                    "UPDATE document_artifacts SET acquisition_status=? WHERE artifact_id=?",
                    (status, artifact_id),
                )
    return PersistedDownload(
        download_attempt_id=attempt_id,
        artifact_id=artifact_id,
        artifact_source_ids=tuple(int(row["artifact_source_id"]) for row in sources),
        requested_url=result.requested_url,
        final_url=result.final_url,
        result_status=result.result_status,
        http_status=result.http_status,
        declared_media_type=result.declared_media_type,
        detected_media_type=result.detected_media_type,
        byte_size=result.byte_size,
        content_sha256=digest,
        blob_id=blob_id,
        content_id=content_id,
        redirect_count=len(result.redirects),
        started_at_utc=started_at_utc,
        fetched_at_utc=fetched_at_utc,
        elapsed_ms=result.elapsed_ms,
        response_headers=dict(result.response_headers),
        redirect_chain=tuple(item.to_dict() for item in result.redirects),
        source_provenance=tuple(
            {
                "artifact_source_id": int(row["artifact_source_id"]),
                "discovered_from_source": row["discovered_from_source"],
                "discovery_page_url": row["discovery_page_url"],
                "document_url": row["document_url"],
                "is_primary_source": bool(row["is_primary_source"]),
            }
            for row in sources
        ),
        error={
            "code": result.error_code,
            "message": result.error_message,
        }
        if result.error_code or result.error_message
        else {},
    )
