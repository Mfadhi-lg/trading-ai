"""Command-line entry point for the implemented vertical slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    DB_PATH,
    DOCUMENT_MAX_RESPONSE_BYTES,
    DOCUMENT_TOTAL_TIMEOUT_SECONDS,
    MF_COVERAGE_EXPORTS,
    MF_FEATURE_EXPORTS,
    MF_WORKBOOK_EXPORTS,
    SCREENER_WORKER_LIMIT,
)
from .db.database import connect, init_db
from .db.mf_feature_repo import (
    FEATURE_SCOPES,
    derive_stock_month_features_through,
)
from .db.repo import utc_now
from .exports.mf_feature_json_export import write_mf_feature_export
from .exports.mf_coverage_report import write_mf_coverage_report
from .exports.quality_report import write_quality_report
from .exports.document_quality_report import write_document_quality_report
from .exports.artifact_coverage import write_artifact_coverage_report
from .exports.screener_discovery_coverage import write_screener_discovery_coverage
from .exports.exchange_universe_coverage import write_exchange_universe_coverage
from .pipeline.exchange_universe import (
    RESULT_FILING_DATASETS,
    UNIVERSE_DATASETS,
    discover_exchange_results,
    refresh_exchange_universe,
)
from .pipeline.screener_vertical import scrape_screener_company
from .pipeline.screener_vertical import reprocess_screener_blobs
from .pipeline.screener_discovery import (
    discover_screener_results,
    run_screener_company_worker,
)
from .pipeline.trendlyne_vertical import scrape_trendlyne_company
from .pipeline.trendlyne_pilot import (
    PILOT_SYMBOLS,
    build_trendlyne_pilot_plan,
    register_trendlyne_pilot_plan,
    run_trendlyne_pilot_batch,
)
from .pipeline.import_mf import (
    execute_import,
    import_plan_payload,
    plan_directory_import,
    plan_import,
)
from .pipeline.crosscheck_mfdata import execute_mfdata_crosscheck
from .pipeline.discover_documents import discover_documents
from .pipeline.fetch_documents import fetch_documents
from .pipeline.extract_document import extract_document
from .pipeline.normalize_credit_rating import normalize_credit_rating_document
from .pipeline.ingest_official_transcript import ingest_official_transcript
from .pipeline.normalize_recording import normalize_recording
from .pipeline.transcribe_recording import transcribe_recording
from .pipeline.reconcile_artifacts import reconcile_artifacts
from .documents.downloader import ALLOWED_MEDIA_BY_ARTIFACT, DownloadPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scraper")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init-db", help="Apply SQLite migrations")
    init.add_argument("--db", type=Path, default=DB_PATH)

    scrape = subcommands.add_parser("scrape-one", help="Fetch, parse, validate, accept, export")
    scrape.add_argument("--source", choices=["screener", "trendlyne"], required=True)
    scrape.add_argument("--symbol", required=True)
    scrape.add_argument(
        "--scope", choices=["consolidated", "standalone"], default="consolidated"
    )
    scrape.add_argument(
        "--period",
        default="latest",
        help="Trendlyne month as YYYY-MM, or latest (default)",
    )
    scrape.add_argument("--db", type=Path, default=DB_PATH)

    quality = subcommands.add_parser(
        "quality-report",
        help="Export accepted, rejected, and manual-review quality outcomes",
    )
    quality.add_argument("--db", type=Path, default=DB_PATH)
    quality.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: store/exports/quality-report.json)",
    )

    document_quality = subcommands.add_parser(
        "document-quality-report",
        help="Report D08 artifact coverage, fallbacks, OCR, failures, and conflicts",
    )
    document_quality.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Limit to an NSE symbol; repeat for multiple pilot companies",
    )
    document_quality.add_argument("--output", type=Path)
    document_quality.add_argument("--db", type=Path, default=DB_PATH)

    reconcile = subcommands.add_parser(
        "reconcile-artifacts",
        help="Claim and check only due delayed-artifact expectations",
    )
    reconcile.add_argument("--due", action="store_true", required=True)
    reconcile.add_argument("--limit", type=int, default=100)
    reconcile.add_argument("--dry-run", action="store_true")
    reconcile.add_argument("--output", type=Path)
    reconcile.add_argument("--db", type=Path, default=DB_PATH)

    artifact_coverage = subcommands.add_parser(
        "artifact-coverage",
        help="Report delayed-artifact coverage and persisted retry state",
    )
    artifact_coverage.add_argument(
        "--symbol", action="append", dest="symbols",
        help="Limit to an NSE symbol; repeat for multiple companies",
    )
    artifact_coverage.add_argument("--output", type=Path)
    artifact_coverage.add_argument("--db", type=Path, default=DB_PATH)

    reprocess = subcommands.add_parser(
        "reprocess-screener",
        help="Reparse saved immutable Screener company and peer blobs without fetching",
    )
    reprocess.add_argument("--symbol", required=True)
    reprocess.add_argument(
        "--scope", choices=["consolidated", "standalone"], default="consolidated"
    )
    reprocess.add_argument("--blob-id", type=int, required=True)
    reprocess.add_argument("--peer-blob-id", type=int, required=True)
    reprocess.add_argument("--db", type=Path, default=DB_PATH)

    screen_discovery = subcommands.add_parser(
        "screener-discover-results",
        help="Parse or fetch one authenticated result screen and enqueue complete coverage",
    )
    screen_discovery.add_argument("--query-role", required=True)
    screen_discovery.add_argument("--query", required=True)
    screen_discovery.add_argument(
        "--fixture", type=Path,
        help="Saved permitted screen HTML (no network request)",
    )
    screen_discovery.add_argument("--dry-run", action="store_true")
    screen_discovery.add_argument("--db", type=Path, default=DB_PATH)

    screen_worker = subcommands.add_parser(
        "screener-company-worker",
        help="Claim and run a bounded batch through the full Screener pipeline",
    )
    screen_worker.add_argument("--due", action="store_true", required=True)
    screen_worker.add_argument("--limit", type=int, default=SCREENER_WORKER_LIMIT)
    screen_worker.add_argument("--dry-run", action="store_true")
    screen_worker.add_argument("--db", type=Path, default=DB_PATH)

    screen_coverage = subcommands.add_parser(
        "screener-discovery-coverage",
        help="Export result-discovery, queue, acceptance and failure coverage",
    )
    screen_coverage.add_argument("--output", type=Path)
    screen_coverage.add_argument("--db", type=Path, default=DB_PATH)

    universe = subcommands.add_parser(
        "exchange-refresh-universe",
        help="Fold one dated NSE/BSE security master or change file into the registry",
    )
    universe.add_argument("--dataset", choices=list(UNIVERSE_DATASETS), required=True)
    universe.add_argument(
        "--file", type=Path, help="Operator-downloaded official file (no network request)"
    )
    universe.add_argument("--url", help="Official exchange dataset URL (allowlisted)")
    universe.add_argument(
        "--effective-date", help="Snapshot date as YYYY-MM-DD (default: today, UTC)"
    )
    universe.add_argument("--dry-run", action="store_true")
    universe.add_argument("--db", type=Path, default=DB_PATH)

    exchange_results = subcommands.add_parser(
        "exchange-discover-results",
        help="Ingest an official result feed and queue only advanced reporting periods",
    )
    exchange_results.add_argument(
        "--dataset", choices=list(RESULT_FILING_DATASETS), required=True
    )
    exchange_results.add_argument("--file", type=Path)
    exchange_results.add_argument("--url")
    exchange_results.add_argument("--effective-date")
    exchange_results.add_argument(
        "--result-period",
        help="Keep only filings for YYYY-MM when an official feed spans periods",
    )
    exchange_results.add_argument("--dry-run", action="store_true")
    exchange_results.add_argument("--db", type=Path, default=DB_PATH)

    universe_coverage = subcommands.add_parser(
        "exchange-universe-coverage",
        help="Export registry, change, filing and enqueue coverage",
    )
    universe_coverage.add_argument("--output", type=Path)
    universe_coverage.add_argument("--db", type=Path, default=DB_PATH)

    import_mf = subcommands.add_parser(
        "import-mf",
        help="Import, validate, accept/reject, and export an official-AMC workbook",
    )
    import_mf.add_argument("--publisher", required=True)
    import_mf.add_argument("--month", required=True, help="Disclosure month as YYYY-MM")
    import_mf.add_argument("--path", type=Path, required=True)
    import_mf.add_argument("--dry-run", action="store_true")
    import_mf.add_argument("--db", type=Path, default=DB_PATH)

    import_directory = subcommands.add_parser(
        "import-mf-directory",
        help="Dry-run or resumably execute a local workbook directory import plan",
    )
    import_directory.add_argument("--path", type=Path, required=True)
    import_directory.add_argument("--dry-run", action="store_true")
    import_directory.add_argument("--db", type=Path, default=DB_PATH)

    derive_features = subcommands.add_parser(
        "derive-mf-features",
        help="Derive exact stock-month features from current accepted month changes",
    )
    derive_features.add_argument("--publisher", required=True)
    derive_features.add_argument("--through-month", required=True)
    derive_features.add_argument("--db", type=Path, default=DB_PATH)

    export_features = subcommands.add_parser(
        "export-mf-features",
        help="Export current stock-month features with workbook and delta lineage",
    )
    export_features.add_argument("--scope", choices=FEATURE_SCOPES, required=True)
    export_features.add_argument("--through-month", required=True)
    export_features.add_argument("--publisher")
    export_features.add_argument("--output", type=Path)
    export_features.add_argument("--db", type=Path, default=DB_PATH)

    coverage = subcommands.add_parser(
        "mf-coverage-report",
        help="Report bounded official-workbook, match, pair, and feature coverage",
    )
    coverage.add_argument("--publisher", required=True)
    coverage.add_argument("--through-month", required=True)
    coverage.add_argument("--months", type=int, default=12)
    coverage.add_argument("--output", type=Path)
    coverage.add_argument("--db", type=Path, default=DB_PATH)

    mfdata = subcommands.add_parser(
        "crosscheck-mfdata",
        help="Import a saved mfdata holdings response and compare it to official rows",
    )
    mfdata.add_argument("--publisher", required=True)
    mfdata.add_argument("--scheme-code", required=True)
    mfdata.add_argument("--family-id", required=True, type=int)
    mfdata.add_argument("--month", required=True, help="Disclosure month as YYYY-MM")
    mfdata.add_argument("--path", type=Path, required=True)
    mfdata.add_argument("--output", type=Path)
    mfdata.add_argument("--db", type=Path, default=DB_PATH)

    discover = subcommands.add_parser(
        "discover-documents",
        help="Discover Phase 2 artifact links from saved primary-source pages",
    )
    discover.add_argument("--symbol", required=True)
    discover.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Saved-page discovery manifest; this command never fetches pages",
    )
    discover.add_argument("--dry-run", action="store_true")
    discover.add_argument("--db", type=Path, default=DB_PATH)

    fetch_docs = subcommands.add_parser(
        "fetch-documents",
        help="Boundedly download and signature-validate discovered artifacts",
    )
    fetch_docs.add_argument("--symbol", required=True)
    fetch_docs.add_argument(
        "--artifact-type",
        action="append",
        choices=sorted(ALLOWED_MEDIA_BY_ARTIFACT),
        dest="artifact_types",
        help="Limit artifact type; repeat to select more than one",
    )
    fetch_docs.add_argument(
        "--max-bytes", type=int, default=DOCUMENT_MAX_RESPONSE_BYTES
    )
    fetch_docs.add_argument(
        "--timeout-seconds", type=float, default=DOCUMENT_TOTAL_TIMEOUT_SECONDS
    )
    fetch_docs.add_argument("--output", type=Path)
    fetch_docs.add_argument("--db", type=Path, default=DB_PATH)

    extract_doc = subcommands.add_parser(
        "extract-document",
        help="Extract native PDF/PPT/PPTX structure with page-local OCR fallback",
    )
    extract_doc.add_argument("--artifact-id", type=int, required=True)
    extract_doc.add_argument(
        "--content-id",
        type=int,
        help="Exact immutable content version (default: preferred/latest official)",
    )
    extract_doc.add_argument("--ocr-language", default="eng")
    extract_doc.add_argument("--output", type=Path)
    extract_doc.add_argument("--db", type=Path, default=DB_PATH)

    normalize_rating = subcommands.add_parser(
        "normalize-credit-rating",
        help="Normalize a credit-rating PDF from persisted page evidence",
    )
    normalize_rating.add_argument("--artifact-id", type=int, required=True)
    normalize_rating.add_argument(
        "--extraction-id",
        type=int,
        help="Exact successful D03 extraction (default: preferred/latest official)",
    )
    normalize_rating.add_argument("--output", type=Path)
    normalize_rating.add_argument("--db", type=Path, default=DB_PATH)

    ingest_transcript = subcommands.add_parser(
        "ingest-official-transcript",
        help="Segment an official transcript PDF from persisted page evidence",
    )
    ingest_transcript.add_argument("--artifact-id", type=int, required=True)
    ingest_transcript.add_argument(
        "--extraction-id",
        type=int,
        help="Exact successful D03 extraction (default: preferred/latest official)",
    )
    ingest_transcript.add_argument("--output", type=Path)
    ingest_transcript.add_argument("--db", type=Path, default=DB_PATH)

    normalize_media = subcommands.add_parser(
        "normalize-recording",
        help="Resolve a validated official recording and derive deterministic PCM WAV",
    )
    normalize_media.add_argument("--artifact-id", type=int, required=True)
    normalize_media.add_argument(
        "--content-id",
        type=int,
        help="Exact original media content (default: preferred/latest official)",
    )
    normalize_media.add_argument("--output", type=Path)
    normalize_media.add_argument("--db", type=Path, default=DB_PATH)

    transcribe = subcommands.add_parser(
        "transcribe",
        help="Create an official-first machine transcript from accepted D06 audio",
    )
    transcribe.add_argument(
        "--artifact-id",
        type=int,
        required=True,
        help="Transcript artifact that will own the generated transcript",
    )
    transcribe.add_argument(
        "--recording-artifact-id",
        type=int,
        help="Exact matching recording artifact (default: resolve by event identity)",
    )
    transcribe.add_argument(
        "--model-path",
        type=Path,
        help=(
            "Local faster-whisper model directory, required only when the "
            "official-first gate permits transcription"
        ),
    )
    transcribe.add_argument("--device", default="cpu")
    transcribe.add_argument("--compute-type", default="int8")
    transcribe.add_argument("--language")
    transcribe.add_argument(
        "--comparison-requested",
        action="store_true",
        help="Human-requested comparison even when an official transcript is usable",
    )
    transcribe.add_argument("--output", type=Path)
    transcribe.add_argument("--db", type=Path, default=DB_PATH)

    def add_pilot_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--end-period", required=True, help="Newest exact pilot month as YYYY-MM"
        )
        command.add_argument("--months", type=int, default=12, choices=range(6, 13))
        command.add_argument("--batch-size", type=int, default=3, choices=range(1, 4))
        command.add_argument(
            "--symbol",
            action="append",
            dest="symbols",
            help="Pilot symbol; repeat for multiple companies (default: RBA)",
        )
        command.add_argument("--db", type=Path, default=DB_PATH)

    pilot_plan = subcommands.add_parser(
        "trendlyne-pilot-plan",
        help="Print the exact P03 request plan without fetching or changing state",
    )
    add_pilot_arguments(pilot_plan)
    pilot_plan.add_argument(
        "--persist",
        action="store_true",
        help="Persist the printed plan for coverage reporting without fetching",
    )

    pilot_run = subcommands.add_parser(
        "trendlyne-pilot-backfill",
        help="Print the exact P03 plan, then execute at most one bounded batch",
    )
    add_pilot_arguments(pilot_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init-db":
        conn = connect(args.db)
        try:
            init_db(conn)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "database": str(args.db.resolve())}))
        return 0

    if args.command == "quality-report":
        conn = connect(args.db)
        try:
            init_db(conn)
            output = write_quality_report(conn, args.output)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "output": str(output.resolve())}))
        return 0

    if args.command == "document-quality-report":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                output = write_document_quality_report(
                    conn,
                    args.output,
                    symbols=tuple(args.symbols or ()),
                )
                payload = json.loads(output.read_text(encoding="utf-8"))
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "coverage_status": payload["coverage_status"],
                    "pilot_status": payload["pilot"]["status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return (
            0
            if payload["coverage_status"] == "complete"
            and payload["pilot"]["status"] == "complete"
            else 1
        )

    if args.command == "reconcile-artifacts":
        try:
            payload = reconcile_artifacts(
                due=args.due,
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "artifact-coverage":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                output = write_artifact_coverage_report(
                    conn, args.output, symbols=tuple(args.symbols or ())
                )
                payload = json.loads(output.read_text(encoding="utf-8"))
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "coverage_status": payload["coverage_status"],
                    "queue_status": payload["queue_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "screener-discover-results":
        try:
            payload = discover_screener_results(
                query_role=args.query_role,
                query=args.query,
                fixture=args.fixture,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "screener-company-worker":
        try:
            payload = run_screener_company_worker(
                due=args.due,
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        failed = payload["summary"].get("outcome_counts", {})
        return 1 if any(key.startswith("failed_") and value for key, value in failed.items()) else 0

    if args.command == "screener-discovery-coverage":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                output = write_screener_discovery_coverage(conn, args.output)
                payload = json.loads(output.read_text(encoding="utf-8"))
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "coverage_status": payload["coverage_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command in {"exchange-refresh-universe", "exchange-discover-results"}:
        runner = (
            refresh_exchange_universe
            if args.command == "exchange-refresh-universe"
            else discover_exchange_results
        )
        try:
            runner_options = dict(
                dataset=args.dataset,
                file=args.file,
                url=args.url,
                effective_date=args.effective_date,
                dry_run=args.dry_run,
                db_path=args.db,
            )
            if args.command == "exchange-discover-results":
                runner_options["result_period"] = args.result_period
            payload = runner(**runner_options)
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        # Filings that cannot be resolved to a company are a discovery gap, not a
        # technical failure, and must be visible in the exit status.
        review = (payload.get("enqueue") or {}).get("manual_review", 0)
        return 1 if review else 0

    if args.command == "exchange-universe-coverage":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                output = write_exchange_universe_coverage(conn, args.output)
                payload = json.loads(output.read_text(encoding="utf-8"))
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "coverage_status": payload["coverage_status"],
                    "missing_required_datasets": payload["missing_required_datasets"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "derive-mf-features":
        conn = connect(args.db)
        try:
            init_db(conn)
            results = derive_stock_month_features_through(
                conn,
                publisher=args.publisher,
                through_month=args.through_month,
                generated_at_utc=utc_now(),
            )
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok",
            "publisher": args.publisher,
            "through_month": args.through_month,
            "feature_sets": [item.to_dict() for item in results],
        }, sort_keys=True))
        return 0

    if args.command == "export-mf-features":
        publisher_path = args.publisher or "all_publishers"
        output = args.output or (
            MF_FEATURE_EXPORTS / publisher_path / args.scope
            / f"through-{args.through_month}.json"
        )
        conn = connect(args.db)
        try:
            init_db(conn)
            written = write_mf_feature_export(
                conn,
                scope=args.scope,
                through_month=args.through_month,
                publisher=args.publisher,
                output=output,
            )
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok", "output": str(written.resolve()),
            "scope": args.scope, "through_month": args.through_month,
        }, sort_keys=True))
        return 0

    if args.command == "mf-coverage-report":
        output = args.output or (
            MF_COVERAGE_EXPORTS / args.publisher
            / f"through-{args.through_month}.json"
        )
        conn = connect(args.db)
        try:
            init_db(conn)
            written = write_mf_coverage_report(
                conn,
                publisher=args.publisher,
                through_month=args.through_month,
                month_count=args.months,
                output=output,
            )
            payload = json.loads(written.read_text(encoding="utf-8"))
        finally:
            conn.close()
        print(json.dumps({
            "status": "ok",
            "coverage_status": payload["coverage_status"],
            "output": str(written.resolve()),
            "summary": payload["summary"],
        }, sort_keys=True))
        return 0 if payload["coverage_status"] == "complete" else 1

    if args.command == "crosscheck-mfdata":
        try:
            payload = execute_mfdata_crosscheck(
                publisher=args.publisher,
                scheme_code=args.scheme_code,
                family_id=args.family_id,
                disclosure_month=args.month,
                path=args.path,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "discover-documents":
        try:
            payload = discover_documents(
                symbol=args.symbol,
                manifest_path=args.manifest,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "fetch-documents":
        try:
            if args.max_bytes > DOCUMENT_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"--max-bytes cannot exceed configured bound "
                    f"{DOCUMENT_MAX_RESPONSE_BYTES}"
                )
            if args.timeout_seconds > DOCUMENT_TOTAL_TIMEOUT_SECONDS:
                raise ValueError(
                    f"--timeout-seconds cannot exceed configured bound "
                    f"{DOCUMENT_TOTAL_TIMEOUT_SECONDS}"
                )
            payload = fetch_documents(
                symbol=args.symbol,
                artifact_types=tuple(args.artifact_types or ()),
                db_path=args.db,
                output=args.output,
                policy=DownloadPolicy(
                    max_bytes=args.max_bytes,
                    total_timeout_seconds=args.timeout_seconds,
                ),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["summary"]["rejected_url_count"] == 0 else 1

    if args.command == "extract-document":
        try:
            from .documents.ocr import TesseractOcr

            payload = extract_document(
                artifact_id=args.artifact_id,
                content_id=args.content_id,
                db_path=args.db,
                output=args.output,
                ocr_engine=TesseractOcr(language=args.ocr_language),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["extraction"]["extraction_status"] == "ok" else 1

    if args.command == "normalize-credit-rating":
        try:
            payload = normalize_credit_rating_document(
                artifact_id=args.artifact_id,
                extraction_id=args.extraction_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return (
            0
            if payload["normalization"]["normalization_status"] == "accepted"
            else 1
        )

    if args.command == "ingest-official-transcript":
        try:
            payload = ingest_official_transcript(
                artifact_id=args.artifact_id,
                extraction_id=args.extraction_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0 if payload["transcript"]["validation_status"] == "accepted" else 1

    if args.command == "normalize-recording":
        try:
            payload = normalize_recording(
                artifact_id=args.artifact_id,
                content_id=args.content_id,
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return (
            0
            if payload["normalization"]["normalization_status"] == "accepted"
            else 1
        )

    if args.command == "transcribe":
        try:
            from .documents.transcription import FasterWhisperTranscriber

            payload = transcribe_recording(
                artifact_id=args.artifact_id,
                recording_artifact_id=args.recording_artifact_id,
                comparison_requested=args.comparison_requested,
                transcriber=FasterWhisperTranscriber(
                    args.model_path,
                    device=args.device,
                    compute_type=args.compute_type,
                    language=args.language,
                ),
                db_path=args.db,
                output=args.output,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        transcription = payload["transcription"]
        if transcription is None:
            return 0
        return 0 if transcription["run_status"] == "accepted" else 1

    if args.command in {"import-mf", "import-mf-directory"}:
        try:
            if args.command == "import-mf":
                proposals = plan_import(
                    publisher=args.publisher,
                    disclosure_month=args.month,
                    path=args.path,
                    db_path=args.db,
                )
            else:
                proposals = plan_directory_import(path=args.path, db_path=args.db)
            payload = (
                import_plan_payload(proposals, dry_run=True)
                if args.dry_run
                else execute_import(
                    proposals, db_path=args.db, export_root=MF_WORKBOOK_EXPORTS
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 1 if any(
            item["proposed_action"] == "quarantine"
            or (
                not args.dry_run
                and item.get("import_state") in {"rejected", "quarantine"}
            )
            for item in payload["files"]
        ) else 0

    if args.command == "trendlyne-pilot-plan":
        conn = connect(args.db)
        try:
            init_db(conn)
            plan = build_trendlyne_pilot_plan(
                conn,
                end_source_period=args.end_period,
                month_count=args.months,
                batch_size=args.batch_size,
                symbols=tuple(args.symbols or PILOT_SYMBOLS),
            )
        finally:
            conn.close()
        print(json.dumps({"event": "request_plan", **plan.to_dict()}, sort_keys=True))
        if args.persist:
            plan_id, _ = register_trendlyne_pilot_plan(
                end_source_period=args.end_period,
                month_count=args.months,
                batch_size=args.batch_size,
                symbols=tuple(args.symbols or PILOT_SYMBOLS),
                db_path=args.db,
            )
            print(json.dumps({"event": "plan_registered", "plan_id": plan_id}))
        return 0

    if args.command == "trendlyne-pilot-backfill":
        result = run_trendlyne_pilot_batch(
            end_source_period=args.end_period,
            month_count=args.months,
            batch_size=args.batch_size,
            symbols=tuple(args.symbols or PILOT_SYMBOLS),
            db_path=args.db,
            on_plan=lambda plan: print(
                json.dumps({"event": "request_plan", **plan.to_dict()}, sort_keys=True),
                flush=True,
            ),
        )
        print(json.dumps({"event": "batch_result", **result.to_dict()}, sort_keys=True))
        return 1 if result.failed_request_count else 0

    if args.command == "reprocess-screener":
        result = reprocess_screener_blobs(
            args.symbol,
            args.scope,
            args.blob_id,
            args.peer_blob_id,
            db_path=args.db,
        )
    elif args.source == "screener":
        result = scrape_screener_company(
            args.symbol,
            args.scope,
            db_path=args.db,
        )
    else:
        result = scrape_trendlyne_company(
            args.symbol,
            requested_source_period=args.period,
            db_path=args.db,
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "run_id": result.run_id,
                "fetch_id": result.fetch_id,
                "blob_id": result.blob_id,
                "parse_id": result.parse_id,
                "company_id": result.company_id,
                "reused_parse": result.reused_parse,
                "export_path": str(result.export_path.resolve()),
                **(
                    {
                        "requested_source_period": result.requested_source_period,
                        "resolved_source_period": result.resolved_source_period,
                    }
                    if args.command == "scrape-one" and args.source == "trendlyne"
                    else {}
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
