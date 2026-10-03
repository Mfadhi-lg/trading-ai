"""Fail-closed health checks for the separately mounted document byte store."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import (
    DOCUMENT_MIN_FREE_BYTES,
    DOCUMENT_MIN_FREE_PERCENT,
    DOCUMENT_REQUIRE_SENTINEL,
    DOCUMENT_STORE,
    DOCUMENT_STORE_CAPACITY_FILE,
    DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS,
    DOCUMENT_STORE_REQUIRE_CAPACITY_FILE,
    DOCUMENT_STORE_SENTINEL,
    assert_configured_writable_targets,
)


@dataclass(frozen=True)
class DocumentStoreHealth:
    root: Path
    total_bytes: int
    free_bytes: int
    required_free_bytes: int
    # Which reading the reserve was computed from, so a report can never imply
    # the external device was measured when only the container's view was.
    capacity_source: str = "statvfs"
    capacity_measured_at_utc: str | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "root": str(self.root),
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "required_free_bytes": self.required_free_bytes,
            "capacity_source": self.capacity_source,
            "capacity_measured_at_utc": self.capacity_measured_at_utc,
        }


def _published_capacity(
    root: Path, *, max_age_seconds: int, required: bool
) -> tuple[int, int, str] | None:
    """Read the host's own measurement of the device backing ``root``."""

    path = root / DOCUMENT_STORE_CAPACITY_FILE
    if not path.is_file():
        if required:
            raise RuntimeError(
                f"Document-store capacity file is missing: {path}; the container's "
                "own free-space reading is not trusted for this mount"
            )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total = int(payload["total_bytes"])
        free = int(payload["free_bytes"])
        measured_raw = str(payload["measured_at_utc"])
        measured = dt.datetime.fromisoformat(measured_raw)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Document-store capacity file is unreadable: {path} ({exc})"
        ) from exc
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - measured).total_seconds()
    if age > max_age_seconds:
        raise RuntimeError(
            f"Document-store capacity file is stale: {path} measured {int(age)}s ago, "
            f"limit {max_age_seconds}s"
        )
    if total <= 0 or free < 0 or free > total:
        raise RuntimeError(
            f"Document-store capacity file is incoherent: total={total} free={free}"
        )
    return total, free, measured_raw


def assert_document_store_ready(
    root: Path = DOCUMENT_STORE,
    *,
    require_sentinel: bool = DOCUMENT_REQUIRE_SENTINEL,
    min_free_bytes: int = DOCUMENT_MIN_FREE_BYTES,
    min_free_percent: float = DOCUMENT_MIN_FREE_PERCENT,
    require_capacity_file: bool = DOCUMENT_STORE_REQUIRE_CAPACITY_FILE,
    capacity_max_age_seconds: int = DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS,
) -> DocumentStoreHealth:
    """Verify the real mount before a worker is permitted to write report bytes."""

    root = root.resolve()
    assert_configured_writable_targets(document_path=root)
    if not root.is_dir():
        raise RuntimeError(f"Document store does not exist: {root}")
    sentinel = root / DOCUMENT_STORE_SENTINEL
    if require_sentinel and not sentinel.is_file():
        raise RuntimeError(f"Document-store sentinel is missing: {sentinel}")
    if not os.access(root, os.W_OK | os.X_OK):
        raise RuntimeError(f"Document store is not writable: {root}")
    usage = shutil.disk_usage(root)
    published = _published_capacity(
        root, max_age_seconds=capacity_max_age_seconds, required=require_capacity_file
    )
    if published is None:
        total, free, source, measured_at = usage.total, usage.free, "statvfs", None
    else:
        total, free, measured_at = published
        source = "host_published"
    percent_reserve = int(total * (min_free_percent / 100.0))
    required = max(min_free_bytes, percent_reserve)
    if free < required:
        raise RuntimeError(
            f"Document store has {free} free bytes; {required} are reserved "
            f"(measured via {source})"
        )
    # A permission bit check is insufficient on a disconnected/read-only mount.
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=".document-store-health.", dir=root, delete=False
    )
    probe = Path(handle.name)
    try:
        with handle:
            handle.write(b"trading-ai-document-store-health\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        probe.unlink(missing_ok=True)
    return DocumentStoreHealth(root, total, free, required, source, measured_at)
