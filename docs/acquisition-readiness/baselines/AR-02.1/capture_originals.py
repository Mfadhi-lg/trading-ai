"""Read retained bytes only; no application imports, SQLite access or requests."""
import hashlib
import json
from pathlib import Path

targets = [
    (266, 128211, "2026-10-01", "cfbc3d1f66e23620e1b0d1ec43b0b0409c25557d6e02f533b78e9f1babf42e82", 263),
    (261, 141470, "2026-10-03", "dfa299c4a21d31bc29aacc51b50cd516e92d059c6aa1c74d2d69330782e4f869", 1611721),
]
report = {"source_mount": "/source", "production_write_mount": False,
          "network_requests": 0, "application_imports": False, "files": []}
for snapshot, blob, day, expected, size in targets:
    path = Path("/source/snapshots/exchange/NSE/nse_financial_results") / day / (expected + ".csv")
    record = {"snapshot_id": snapshot, "blob_id": blob, "source_path": str(path),
              "expected_sha256": expected, "expected_bytes": size}
    if not path.is_file() or path.is_symlink():
        record["status"] = "MISSING_OR_NOT_REGULAR"
    else:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        actual = hashlib.sha256(data).hexdigest()
        record.update(actual_sha256=actual, actual_bytes=len(data),
                      source_stat_before=[before.st_ino, before.st_size, before.st_mtime_ns],
                      source_stat_after=[after.st_ino, after.st_size, after.st_mtime_ns])
        if before != after or actual != expected or len(data) != size:
            record["status"] = "IDENTITY_MISMATCH_STOP"
        else:
            dest = Path("/evidence/originals") / path.name
            if dest.exists():
                raise RuntimeError("Refuse to overwrite captured original")
            dest.write_bytes(data)
            record.update(status="HASH_VERIFIED_ORIGINAL", retained_file="originals/" + path.name)
    report["files"].append(record)
Path("/evidence/original-capture.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
