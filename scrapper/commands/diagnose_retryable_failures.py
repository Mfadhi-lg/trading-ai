"""DA-FINAL-OPS §5: classify the RETRYABLE_FAILURE bucket before touching retry policy.

Read-only. Groups the live download queue by failure family, host, artifact type and
attempt count so the operator can tell a deterministic code defect apart from normal
distributed source failure / host throttling.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Historical import path for operator scripts and classification helpers.
from ..documents.recovery.diagnostics import _FAMILIES, _family, _host, _verdict
from ..exports.retryable_failure_diagnosis import render_markdown


def diagnose(db_path: str, scope_id: str | None) -> dict:
    from ..documents.recovery.diagnostics import diagnose as build
    return build(db_path, scope_id, _hooks=sys.modules[__name__])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/scraper.db")
    parser.add_argument("--scope-id", default=None)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    from ..config import assert_configured_writable_targets
    assert_configured_writable_targets(Path(args.output_dir), db_path=Path(args.db))

    report = diagnose(args.db, args.scope_id)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "RETRYABLE_FAILURE_DIAGNOSIS.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    (out / "RETRYABLE_FAILURE_DIAGNOSIS.md").write_text(render_markdown(report))
    print(json.dumps({
        "total_retryable": report["total_retryable"],
        "never_attempted": report["never_attempted"],
        "dominant_host": report["dominant_host"],
        "dominant_family": report["dominant_family"],
    }, indent=2))


if __name__ == "__main__":
    main()
