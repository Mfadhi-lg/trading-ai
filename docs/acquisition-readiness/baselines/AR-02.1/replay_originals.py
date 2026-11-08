"""Offline parser/guard replay; immutable retained DB, no fetch or write APIs."""
import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path

from scrapper.sources.exchange.result_filings import parse_result_filings
from scrapper.sources.exchange.snapshot_guard import evaluate_snapshot
from scrapper.workflows.exchange.universe import _evaluate, thresholds_for_dataset

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]
metadata = json.loads((ROOT / "stored-evidence.json").read_text())
db = REPO / "artifacts/acquisition-readiness/AR-01.3/pre-migration-068/raw/scraper.db"
assert not any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))
conn = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA query_only=ON")
report = {"classification": "OFFLINE_ORIGINAL_REPLAY", "db_copy": str(db),
          "db_whole_file_hash_recomputed": False, "production_writes": 0,
          "network_requests": 0, "originals": [], "derived_fixture_cases": []}
parsed_good = None
for item in metadata["snapshots"][:2]:
    content = (ROOT / "originals" / (item["content_sha256"] + ".csv")).read_bytes()
    assert len(content) == item["byte_size"]
    assert hashlib.sha256(content).hexdigest() == item["content_sha256"]
    blob = dict(conn.execute("SELECT * FROM response_blobs WHERE blob_id=?", (item["blob_id"],)).fetchone())
    snapshot = dict(conn.execute("SELECT * FROM exchange_source_snapshots WHERE snapshot_id=?", (item["snapshot_id"],)).fetchone())
    fetch = dict(conn.execute("SELECT * FROM fetch_observations WHERE fetch_id=?", (item["fetch_id"],)).fetchone())
    assert blob["content_sha256"] == item["content_sha256"] and blob["byte_size"] == len(content)
    assert snapshot["blob_id"] == item["blob_id"] and fetch["blob_id"] == item["blob_id"]
    assert fetch["requested_url"] == item["requested_url"] and fetch["http_status"] == 200
    period = "2026-09" if item["snapshot_id"] == 266 else "2026-06"
    parsed = parse_result_filings(content, item["dataset"], result_period=period)
    assert (parsed.accepted_row_count, parsed.rejected_row_count) == (item["accepted_row_count"], item["rejected_row_count"])
    verdict = _evaluate(conn, parsed, dataset=item["dataset"], effective_date=item["effective_date"],
                        observed_at_utc=item["observed_at_utc"], thresholds=thresholds_for_dataset(item["dataset"]),
                        override_validation=False, exclude_snapshot_id=item["snapshot_id"])
    old = evaluate_snapshot(verdict.metrics, verdict.baseline, verdict.thresholds, today=item["observed_at_utc"][:10])
    assert old.as_dict()["findings"] == json.loads(item["findings_json"])
    assert verdict.thresholds.as_dict() == json.loads(item["thresholds_json"])
    assert verdict.metrics.as_dict() == json.loads(item["metrics_json"])
    expected_codes = {"row_count_floor"} if item["snapshot_id"] == 266 else set()
    assert set(verdict.codes) == expected_codes
    record = {"snapshot_id": item["snapshot_id"], "sha256": item["content_sha256"], "bytes": len(content),
              "blob": blob, "snapshot": snapshot, "fetch": fetch,
              "parsed_accepted": parsed.accepted_row_count, "parsed_rejected": parsed.rejected_row_count,
              "excluded_rows": parsed.excluded_row_count, "before_UTC": old.as_dict(), "after_IST": verdict.as_dict()}
    if parsed.rejected:
        record["parser_rejections"] = [{"row_position": r.row_position, "reason_code": r.reason_code, "detail": r.reason_detail} for r in parsed.rejected]
    report["originals"].append(record)
    if parsed.accepted_row_count:
        parsed_good, good_verdict = parsed, verdict

assert parsed_good is not None
# Explicit controlled derivative: one authentic accepted row, serialized anew.
out = io.StringIO(newline="")
writer = csv.writer(out)
writer.writerow(parsed_good.headers)
writer.writerow(parsed_good.filings[0].raw_cells)
sparse_bytes = out.getvalue().encode("utf-8-sig")
sparse = parse_result_filings(sparse_bytes, "nse_financial_results", result_period="2026-06")
assert sparse.accepted_row_count == 1 and sparse.rejected_row_count == 0
for name, observed, effective, expected in [
    ("legitimate_sparse_after_IST_midnight", "2026-10-03T18:44:56+00:00", "2026-10-04", set()),
    ("exact_IST_midnight", "2026-10-03T18:30:00+00:00", "2026-10-04", set()),
    ("next_day_before_IST_midnight", "2026-10-03T18:29:59+00:00", "2026-10-04", {"future_effective_date"}),
    ("truly_future_after_IST_midnight", "2026-10-03T18:44:56+00:00", "2026-10-05", {"future_effective_date"}),
]:
    v = _evaluate(conn, sparse, dataset="nse_financial_results", effective_date=effective,
                  observed_at_utc=observed, thresholds=good_verdict.thresholds,
                  override_validation=False, exclude_snapshot_id=266)
    assert set(v.codes) == expected
    report["derived_fixture_cases"].append({"name": name, "classification": "CONTROLLED_DERIVATIVE_NOT_ORIGINAL",
        "sha256": hashlib.sha256(sparse_bytes).hexdigest(), "observed": observed, "effective": effective,
        "verdict": v.as_dict()})

# Malformed derivative: valid source header and deliberately incomplete row.
out = io.StringIO(newline="")
writer = csv.writer(out); writer.writerow(parsed_good.headers); writer.writerow(parsed_good.filings[0].raw_cells[:-1])
try:
    parse_result_filings(out.getvalue().encode("utf-8-sig"), "nse_financial_results")
except ValueError as exc:
    assert "cells; expected" in str(exc)
    report["derived_fixture_cases"].append({"name": "truncated_row", "classification": "CONTROLLED_DERIVATIVE_NOT_ORIGINAL", "parser_error": str(exc)})
else:
    raise AssertionError("Malformed derivative was accepted")

try:
    _evaluate(conn, sparse, dataset="nse_financial_results", effective_date="2026-10-04",
              observed_at_utc="2026-10-03T18:44:56", thresholds=good_verdict.thresholds,
              override_validation=False, exclude_snapshot_id=266)
except ValueError as exc:
    assert "must include a timezone" in str(exc)
    report["derived_fixture_cases"].append({"name": "naive_observation", "error": str(exc)})
else:
    raise AssertionError("Naive observation accepted")
conn.close()
report["status"] = "PASS"
(ROOT / "original-replay.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"status": report["status"], "originals": [{"snapshot_id": r["snapshot_id"],
    "accepted": r["parsed_accepted"], "rejected": r["parsed_rejected"], "before": r["before_UTC"]["status"],
    "after": r["after_IST"]["status"], "remaining_findings": [f["code"] for f in r["after_IST"]["findings"]]}
    for r in report["originals"]], "derived_fixture_cases": len(report["derived_fixture_cases"])}))
