from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scrapper.config import DOCUMENT_STORE_CAPACITY_FILE, DOCUMENT_STORE_SENTINEL
from scrapper.documents.store import assert_document_store_ready

GIB = 1024**3


def _iso(offset_seconds: int = 0) -> str:
    moment = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=offset_seconds)
    return moment.isoformat()


class DocumentStoreCapacityGuardTests(unittest.TestCase):
    """A bind mount can answer statvfs for a filesystem that holds none of the bytes.

    Docker Desktop reports the macOS root volume for a /Volumes bind mount. In this
    deployment that volume is within 1% of the external disk's total size but shows
    ~320 GiB more free, so the reserve is computed against a disk nobody writes to
    and never trips. The host's own reading has to win, and its absence or staleness
    has to stop the worker rather than fall back to the untrusted number.
    """

    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp())
        (self._root / DOCUMENT_STORE_SENTINEL).write_text("sentinel\n")
        self.addCleanup(shutil.rmtree, self._root, ignore_errors=True)

    def _publish(self, *, total: int, free: int, measured_at: str | None = None) -> None:
        (self._root / DOCUMENT_STORE_CAPACITY_FILE).write_text(
            json.dumps(
                {
                    "total_bytes": total,
                    "free_bytes": free,
                    "measured_at_utc": measured_at or _iso(),
                }
            )
        )

    def _ready(self, **kwargs):
        defaults = {
            "require_sentinel": True,
            "min_free_bytes": 0,
            "min_free_percent": 15.0,
            "require_capacity_file": True,
        }
        return assert_document_store_ready(self._root, **{**defaults, **kwargs})

    def test_published_reading_is_preferred_over_local_statvfs(self) -> None:
        # Below the 15% reserve on the real device, whatever the container believes.
        self._publish(total=1000 * GIB, free=100 * GIB)
        with self.assertRaises(RuntimeError) as caught:
            self._ready()
        self.assertIn("are reserved", str(caught.exception))
        self.assertIn("host_published", str(caught.exception))

    def test_published_reading_above_reserve_is_accepted(self) -> None:
        self._publish(total=1000 * GIB, free=400 * GIB)
        health = self._ready()
        self.assertEqual(health.free_bytes, 400 * GIB)
        self.assertEqual(health.capacity_source, "host_published")

    def test_missing_capacity_file_fails_closed_when_required(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            self._ready()
        self.assertIn("capacity file is missing", str(caught.exception))

    def test_stale_capacity_file_fails_closed(self) -> None:
        self._publish(total=1000 * GIB, free=400 * GIB, measured_at=_iso(-7200))
        with self.assertRaises(RuntimeError) as caught:
            self._ready(capacity_max_age_seconds=3600)
        self.assertIn("stale", str(caught.exception))

    def test_unreadable_capacity_file_fails_closed(self) -> None:
        (self._root / DOCUMENT_STORE_CAPACITY_FILE).write_text("not json")
        with self.assertRaises(RuntimeError) as caught:
            self._ready()
        self.assertIn("unreadable", str(caught.exception))

    def test_incoherent_capacity_file_fails_closed(self) -> None:
        self._publish(total=100 * GIB, free=900 * GIB)
        with self.assertRaises(RuntimeError) as caught:
            self._ready()
        self.assertIn("incoherent", str(caught.exception))

    def test_statvfs_is_still_used_when_no_file_is_required(self) -> None:
        health = assert_document_store_ready(
            self._root,
            require_sentinel=True,
            min_free_bytes=0,
            min_free_percent=0.0,
            require_capacity_file=False,
        )
        self.assertEqual(health.capacity_source, "statvfs")
        self.assertIsNone(health.capacity_measured_at_utc)

    def test_present_file_wins_even_when_not_required(self) -> None:
        self._publish(total=1000 * GIB, free=400 * GIB)
        health = assert_document_store_ready(
            self._root,
            require_sentinel=True,
            min_free_bytes=0,
            min_free_percent=0.0,
            require_capacity_file=False,
        )
        self.assertEqual(health.capacity_source, "host_published")
        self.assertEqual(health.free_bytes, 400 * GIB)


if __name__ == "__main__":
    unittest.main()
