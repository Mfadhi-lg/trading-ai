"""Publish the document device's true capacity into the store root, from the host.

Runs on the host, never in a container: the point is to record a statvfs taken
where it is trustworthy. Docker Desktop answers statvfs on a ``/Volumes`` bind
mount with the macOS root volume, and here that volume happens to be within 1%
of the external disk's total size while differing by ~320 GiB of free space, so
capacity alone cannot detect the substitution. Workers read this file instead of
their own reading and refuse to run when it is missing or stale.

Depends only on the standard library so it can run under the host interpreter
without the project's virtualenv.

    python3 -m scrapper.commands.publish_document_store_capacity \
        --root "/Volumes/My Passport/passport/trading-ai" --loop 300
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from scrapper.config import assert_configured_writable_targets

CAPACITY_FILE = ".trading_ai_document_store_capacity.json"
SENTINEL = ".trading_ai_document_store"


def publish(root: Path, *, require_sentinel: bool = True) -> dict:
    assert_configured_writable_targets(document_path=root)
    if not root.is_dir():
        raise SystemExit(f"document store does not exist: {root}")
    if require_sentinel and not (root / SENTINEL).is_file():
        raise SystemExit(f"document-store sentinel is missing: {root / SENTINEL}")
    stat = os.statvfs(root)
    payload = {
        "schema_version": "1.0.0",
        "root": str(root),
        "total_bytes": stat.f_blocks * stat.f_frsize,
        # f_bavail, not f_bfree: root-reserved blocks are not ours to spend.
        "free_bytes": stat.f_bavail * stat.f_frsize,
        "measured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "measured_by": "host",
    }
    # Atomic replace: a worker must never read a half-written reading.
    target = root / CAPACITY_FILE
    temp = root / f"{CAPACITY_FILE}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--no-require-sentinel", action="store_true")
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        help="Refresh every N seconds; must stay under the workers' staleness limit.",
    )
    args = parser.parse_args()

    while True:
        payload = publish(args.root, require_sentinel=not args.no_require_sentinel)
        gib = 1024**3
        print(
            json.dumps(
                {
                    "free_gib": round(payload["free_bytes"] / gib, 1),
                    "total_gib": round(payload["total_bytes"] / gib, 1),
                    "measured_at_utc": payload["measured_at_utc"],
                }
            ),
            flush=True,
        )
        if args.loop <= 0:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
