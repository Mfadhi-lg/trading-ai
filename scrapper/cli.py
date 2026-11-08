"""Command-line entry point for the implemented vertical slice."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .config import (
    DB_PATH,
    DOCUMENT_STORE,
    DOCUMENT_MAX_RESPONSE_BYTES,
    DOCUMENT_TOTAL_TIMEOUT_SECONDS,
    MF_COVERAGE_EXPORTS,
    MF_FEATURE_EXPORTS,
    MF_WORKBOOK_EXPORTS,
    SCREENER_WORKER_LIMIT,
    THROTTLE_SECONDS,
    assert_configured_writable_targets,
)
from .db.database import connect, init_db
from .db.document_recovery_repo import build_recovery_targets
from .db.mf_feature_repo import (
    FEATURE_SCOPES,
    derive_stock_month_features_through,
)
from .db.exchange_repo import CONFLICT_RESOLUTIONS, resolve_identity_conflict
from .db.repo import utc_now
from .exports.mf_feature_json_export import write_mf_feature_export
from .exports.mf_coverage_report import write_mf_coverage_report
from .exports.quality_report import write_quality_report
from .exports.document_quality_report import write_document_quality_report
from .exports.artifact_coverage import write_artifact_coverage_report
from .exports.screener_discovery_coverage import write_screener_discovery_coverage
from .exports.exchange_reconciliation_report import (
    write_exchange_reconciliation_report,
)
from .exports.exchange_universe_coverage import write_exchange_universe_coverage
from .pipeline.exchange_universe import (
    RESULT_FILING_DATASETS,
    UNIVERSE_DATASETS,
    discover_exchange_results,
    inspect_exchange_dataset,
    refresh_exchange_universe,
)
from .pipeline.scheduler import (
    catch_up_missed_days,
    day_status,
    run_scheduler,
    run_weekly_report,
)
from .sources.exchange.datasets import DATASET_KEYS
from .pipeline.screener_vertical import scrape_screener_company
from .pipeline.screener_vertical import reprocess_screener_blobs
from .pipeline.screener_discovery import (
    discover_screener_results,
    requeue_failed_screener_companies,
    requeue_incomplete_metric_screener_companies,
    run_screener_company_worker,
)
from .db.screener_discovery_repo import REQUEUEABLE_STATUSES
from .view.render import SECTION_CODES
from .view import (
    render_browse,
    render_company,
    render_coverage,
    render_mf,
    render_tables,
    run_download_monitor,
    run_live_monitor,
    run_interactive_shell,
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
from .pipeline.promote_document_links import PROMOTABLE, promote_document_links
from .pipeline.credit_rating_downloads import (
    queue_credit_rating_downloads,
    run_credit_rating_download_worker,
)
from .pipeline.accepted_document_downloads import (
    enqueue_documents_for_all_accepted_parses,
)
from .pipeline.queue_document_downloads import queue_document_downloads
from .pipeline.run_document_download_worker import (
    run_document_download_worker,
    write_download_queue_report,
)
from .pipeline.document_download_recovery import (
    RECOVERY_REASON_CODES,
    import_manual_document,
    register_replacement_document_url,
    requeue_document_downloads,
    write_document_recovery_report,
)
from .pipeline.alternate_source_recovery import run_document_recovery_worker
from .exports.document_storage_projection import write_document_storage_projection
from .exports.document_alternate_source_recovery import (
    write_alternate_source_recovery_report,
)
from .pipeline.extract_document import extract_document
from .pipeline.normalize_credit_rating import normalize_credit_rating_document
from .pipeline.ingest_official_transcript import ingest_official_transcript
from .pipeline.normalize_recording import normalize_recording
from .pipeline.transcribe_recording import transcribe_recording
from .pipeline.reconcile_artifacts import reconcile_artifacts
from .pipeline.reconcile_promoted_artifacts import reconcile_promoted_artifacts
from .pipeline.result_artifacts import backfill_accepted_result_artifacts
from .documents.downloader import ALLOWED_MEDIA_BY_ARTIFACT, DownloadPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scraper")
    subcommands = parser.add_subparsers(dest="command", required=False)

    shell = subcommands.add_parser(
        "shell",
        help="Interactive CLI shell session with slash commands (Claude Code style)",
    )
    shell.add_argument("--db", type=Path, default=DB_PATH)

    init = subcommands.add_parser("init-db", help="Explicitly apply SQLite migrations")
    init.add_argument("--db", type=Path, default=DB_PATH)

    view = subcommands.add_parser(
        "view",
        help="Browse collected data in the terminal (read-only)",
    )
    view_sub = view.add_subparsers(dest="view_mode", required=True)

    company = view_sub.add_parser(
        "company",
        help="One-ticker snapshot: metrics plus every statement across all periods",
    )
    company.add_argument("term", help="NSE ticker, BSE code, or legal-name fragment")
    company.add_argument(
        "--scope", choices=["standalone"], default="standalone",
        help="financial reporting scope (standalone-only)",
    )
    company.add_argument(
        "--periods",
        type=int,
        default=-1,
        help="Period columns to show, most recent first. "
        "0 shows every period; default fits the terminal width.",
    )
    company.add_argument(
        "--sections",
        help="Comma-separated subset of: " + ",".join(SECTION_CODES),
    )
    company.add_argument(
        "--no-shareholding",
        action="store_true",
        help="Skip the shareholding-pattern blocks",
    )
    company.add_argument("--db", type=Path, default=DB_PATH)

    mf = view_sub.add_parser(
        "mf",
        help="Mutual-fund stock-month features (holder flow, quantity changes)",
    )
    mf.add_argument("--publisher", help="Limit to one publisher")
    mf.add_argument("--month", help="Disclosure month as YYYY-MM")
    mf.add_argument(
        "--scope", choices=["active_equity", "all_scheme"], default="active_equity"
    )
    mf.add_argument("--isin", help="Limit to one ISIN")
    mf.add_argument("--limit", type=int, default=25)
    mf.add_argument("--db", type=Path, default=DB_PATH)

    coverage_view = view_sub.add_parser(
        "coverage",
        help="Screener coverage per company plus pipeline queue state",
    )
    coverage_view.add_argument("--db", type=Path, default=DB_PATH)

    tables = view_sub.add_parser(
        "tables",
        help="List tables and views available to browse",
    )
    tables.add_argument("--db", type=Path, default=DB_PATH)

    browse = view_sub.add_parser(
        "browse",
        help="Drill into any table or view with optional WHERE filter",
    )
    browse.add_argument("table")
    browse.add_argument("--where", help="SQL WHERE clause, e.g. 'status = \"accepted\"'")
    browse.add_argument("--limit", type=int, default=25)
    browse.add_argument("--db", type=Path, default=DB_PATH)

    monitor_view = view_sub.add_parser(
        "monitor",
        help="Live real-time pipeline and queue process monitor dashboard",
    )
    monitor_view.add_argument(
        "--refresh",
        type=float,
        default=5.0,
        help="Dashboard refresh interval in seconds (default: 5.0)",
    )
    monitor_view.add_argument("--db", type=Path, default=DB_PATH)

    monitor_top = subcommands.add_parser(
        "monitor",
        help="Live real-time pipeline and queue process monitor dashboard",
    )
    monitor_top.add_argument(
        "--refresh",
        type=float,
        default=5.0,
        help="Dashboard refresh interval in seconds (default: 5.0)",
    )
    monitor_top.add_argument("--db", type=Path, default=DB_PATH)

    download_monitor_view = view_sub.add_parser(
        "downloads",
        help="Live file-download queue, worker, and publisher-health dashboard",
    )
    download_monitor_view.add_argument(
        "--refresh",
        type=float,
        default=5.0,
        help="Dashboard refresh interval in seconds (default: 5.0)",
    )
    download_monitor_view.add_argument("--db", type=Path, default=DB_PATH)

    download_monitor_top = subcommands.add_parser(
        "download-monitor",
        aliases=["monitor-downloads"],
        help="Live file-download queue, worker, and publisher-health dashboard",
    )
    download_monitor_top.add_argument(
        "--refresh",
        type=float,
        default=5.0,
        help="Dashboard refresh interval in seconds (default: 5.0)",
    )
    download_monitor_top.add_argument("--db", type=Path, default=DB_PATH)

    scrape = subcommands.add_parser("scrape-one", help="Fetch, parse, validate, accept, export")
    scrape.add_argument("--source", choices=["screener", "trendlyne"], required=True)
    scrape.add_argument("--symbol", required=True)
    scrape.add_argument(
        "--scope", choices=["consolidated", "standalone"], default="standalone"
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

    result_artifacts = subcommands.add_parser(
        "backfill-result-artifacts",
        help="Require presentation, concall/transcript, recording and rating follow-ups for accepted official results",
    )
    result_artifacts.add_argument("--limit", type=int)
    result_artifacts.add_argument("--db", type=Path, default=DB_PATH)

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

    screen_requeue = subcommands.add_parser(
        "screener-requeue-failed",
        help="Return settled queue failures to pending after a parser or rule fix",
    )
    screen_requeue.add_argument(
        "--status",
        action="append",
        choices=list(REQUEUEABLE_STATUSES),
        help="Failure status to requeue; repeatable. Default: failed_terminal.",
    )
    screen_requeue.add_argument(
        "--path",
        action="append",
        help="Limit to an exact discovered company path; repeatable.",
    )
    screen_requeue.add_argument("--limit", type=int)
    screen_requeue.add_argument("--dry-run", action="store_true")
    screen_requeue.add_argument("--db", type=Path, default=DB_PATH)

    metric_backfill = subcommands.add_parser(
        "screener-requeue-incomplete-metrics",
        help="Requeue acceptances whose run never fetched the custom ratio panel",
    )
    metric_backfill.add_argument(
        "--path",
        action="append",
        help="Limit to an exact discovered company path; repeatable.",
    )
    metric_backfill.add_argument("--limit", type=int)
    metric_backfill.add_argument("--dry-run", action="store_true")
    metric_backfill.add_argument("--db", type=Path, default=DB_PATH)

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
    universe.add_argument(
        "--url",
        help="Official exchange dataset URL (allowlisted); "
        "defaults to the dataset registry's URL when neither --url nor --file is given",
    )
    universe.add_argument(
        "--effective-date", help="Snapshot date as YYYY-MM-DD (default: today, UTC)"
    )
    universe.add_argument("--dry-run", action="store_true")
    universe.add_argument(
        "--override-validation",
        action="store_true",
        help="Apply a snapshot the guard rejected (e.g. a real mass-delisting day)",
    )
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
    exchange_results.add_argument(
        "--override-validation",
        action="store_true",
        help="Ingest a feed the guard rejected",
    )
    exchange_results.add_argument("--db", type=Path, default=DB_PATH)

    resolve_conflict = subcommands.add_parser(
        "exchange-resolve-conflict",
        help="Close one identity conflict blocking a security from being queued",
    )
    resolve_conflict.add_argument("--conflict-id", type=int, required=True)
    resolve_conflict.add_argument(
        "--resolution", choices=list(CONFLICT_RESOLUTIONS), required=True
    )
    resolve_conflict.add_argument("--db", type=Path, default=DB_PATH)

    bulk_enqueue = subcommands.add_parser(
        "exchange-bulk-enqueue",
        help="One-time: enqueue all registry companies not yet in the screener queue",
    )
    bulk_enqueue.add_argument("--dry-run", action="store_true",
                              help="Preview counts without writing anything")
    bulk_enqueue.add_argument("--db", type=Path, default=DB_PATH)

    master_backfill = subcommands.add_parser(
        "exchange-unaccepted-backfill",
        help="Queue only active exchange-master identities lacking an accepted Screener parse",
    )
    master_backfill.add_argument("--result-period", help="Refresh accepted companies missing this quarter, YYYY-MM")
    master_backfill.add_argument("--limit", type=int, default=25)
    master_backfill.add_argument("--dry-run", action="store_true")
    master_backfill.add_argument("--db", type=Path, default=DB_PATH)

    period_coverage = subcommands.add_parser("screener-period-coverage", help="Audit quarter rows, repair period labels, and refresh missing quarters")
    period_coverage.add_argument("--period", required=True)
    period_coverage.add_argument("--symbol", action="append", default=[])
    period_coverage.add_argument("--repair", action="store_true")
    period_coverage.add_argument("--refresh", action="store_true")
    period_coverage.add_argument("--limit", type=int, default=25)
    period_coverage.add_argument("--db", type=Path, default=DB_PATH)

    inspect_dataset = subcommands.add_parser(
        "exchange-inspect-dataset",
        help="Print a dataset's real headers, row count and sample rows; store nothing",
    )
    inspect_dataset.add_argument("--dataset", choices=list(DATASET_KEYS), required=True)
    inspect_dataset.add_argument("--file", type=Path)
    inspect_dataset.add_argument("--url")
    inspect_dataset.add_argument(
        "--result-period", help="Reporting period as YYYY-MM for period-addressed feeds"
    )
    inspect_dataset.add_argument("--sample-rows", type=int, default=3)

    universe_coverage = subcommands.add_parser(
        "exchange-universe-coverage",
        help="Export registry, change, filing and enqueue coverage",
    )
    universe_coverage.add_argument("--output", type=Path)
    universe_coverage.add_argument("--db", type=Path, default=DB_PATH)

    reconciliation = subcommands.add_parser(
        "exchange-reconciliation-report",
        help="Report what an official refresh matched, added and disputed; "
        "exits 1 while any identity conflict is open",
    )
    reconciliation.add_argument("--output", type=Path)
    reconciliation.add_argument(
        "--snapshot-id", type=int, help="Scope to one snapshot (default: latest accepted)"
    )
    reconciliation.add_argument("--db", type=Path, default=DB_PATH)

    scheduler = subcommands.add_parser(
        "exchange-scheduler",
        help="Refresh every exchange dataset on the IST retry ladder",
    )
    scheduler.add_argument(
        "--once", action="store_true", help="Run one attempt now instead of serving"
    )
    scheduler.add_argument(
        "--attempt", type=int, help="Which rung of the ladder --once is running as"
    )
    scheduler.add_argument(
        "--status", action="store_true", help="Report one day's attempts and exit"
    )
    scheduler.add_argument(
        "--weekly-report", action="store_true", help="Run the weekly reconciliation job"
    )
    scheduler.add_argument(
        "--catch-up", action="store_true",
        help="Run final attempts for recent missed trading days and exit",
    )
    scheduler.add_argument(
        "--catch-up-days", type=int, default=7,
        help="Maximum prior calendar days to inspect for --catch-up (default: 7)",
    )
    scheduler.add_argument("--scheduled-for", help="Day to report on as YYYY-MM-DD")
    scheduler.add_argument(
        "--resume-access", choices=[key for key in DATASET_KEYS if key.startswith("bse_")],
        metavar="BSE_DATASET", help="Release one access hold after approved access is confirmed",
    )
    scheduler.add_argument(
        "--resume-evidence", help="Required entitlement or allowlisting evidence for --resume-access",
    )
    scheduler.add_argument("--db", type=Path, default=DB_PATH)

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

    promote_links = subcommands.add_parser(
        "promote-document-links",
        help="Promote accepted Screener document links into D01 artifacts",
    )
    promote_links.add_argument(
        "--document-type", choices=sorted(PROMOTABLE), default="credit_rating"
    )
    promote_links.add_argument("--dry-run", action="store_true")
    promote_links.add_argument("--limit", type=int)
    promote_links.add_argument("--db", type=Path, default=DB_PATH)

    build_recovery = subcommands.add_parser(
        "build-document-recovery-targets",
        help="Build idempotent DA-06 targets for policy-blocked rating artifacts",
    )
    build_recovery.add_argument(
        "--artifact-type", choices=("credit_rating",), default="credit_rating"
    )
    build_recovery.add_argument("--dry-run", action="store_true")
    build_recovery.add_argument("--db", type=Path, default=DB_PATH)

    recovery_worker = subcommands.add_parser(
        "run-document-recovery-worker",
        help="Lease and search bounded official alternate-source recovery targets",
    )
    recovery_worker.add_argument(
        "--source",
        action="append",
        choices=("nse", "bse", "issuer-ir"),
        required=True,
        dest="sources",
        help="Explicit official source group; repeat to select more than one",
    )
    recovery_worker.add_argument("--limit", type=int, default=25)
    recovery_worker.add_argument("--lease-seconds", type=int, default=600)
    recovery_worker.add_argument(
        "--throttle-seconds", type=float, default=THROTTLE_SECONDS
    )
    recovery_worker.add_argument("--db", type=Path, default=DB_PATH)

    alternate_recovery_report = subcommands.add_parser(
        "document-alternate-source-recovery-report",
        help="Report exact DA-06 recovered and residual blocked-rating accounting",
    )
    alternate_recovery_report.add_argument("--output", type=Path)
    alternate_recovery_report.add_argument("--db", type=Path, default=DB_PATH)

    promoted_reconcile = subcommands.add_parser(
        "reconcile-promoted-artifacts",
        help="Immediately append D09 checks for matching promoted/recovered artifacts",
    )
    promoted_reconcile.add_argument("--limit", type=int, default=500)
    promoted_reconcile.add_argument("--dry-run", action="store_true")
    promoted_reconcile.add_argument("--db", type=Path, default=DB_PATH)

    queue_ratings = subcommands.add_parser(
        "queue-credit-rating-downloads",
        help="Materialize the durable queue for promoted credit-rating URLs",
    )
    queue_ratings.add_argument("--db", type=Path, default=DB_PATH)

    rating_worker = subcommands.add_parser(
        "run-credit-rating-download-worker",
        help="Resolve and download a bounded batch of queued credit reports",
    )
    rating_worker.add_argument("--limit", type=int, default=25)
    rating_worker.add_argument(
        "--host", action="append", default=[],
        help="Claim only this reviewed target host; repeat for a host group",
    )
    rating_worker.add_argument("--throttle-seconds", type=float, default=THROTTLE_SECONDS)
    rating_worker.add_argument(
        "--max-bytes", type=int, default=DOCUMENT_MAX_RESPONSE_BYTES
    )
    rating_worker.add_argument(
        "--timeout-seconds", type=float, default=DOCUMENT_TOTAL_TIMEOUT_SECONDS
    )
    rating_worker.add_argument("--db", type=Path, default=DB_PATH)

    queue_docs = subcommands.add_parser(
        "queue-document-downloads",
        help="Register an explicit scope and materialize its durable download jobs",
    )
    queue_docs.add_argument("--manifest", type=Path, required=True)
    queue_docs.add_argument("--priority", type=int, default=100)
    queue_docs.add_argument("--dry-run", action="store_true")
    queue_docs.add_argument("--db", type=Path, default=DB_PATH)

    queue_accepted_docs = subcommands.add_parser(
        "queue-accepted-document-downloads",
        help=(
            "Promote ratings and concall links; queue ratings, PPTs, and transcripts "
            "while retaining recordings as metadata only"
        ),
    )
    queue_accepted_docs.add_argument("--db", type=Path, default=DB_PATH)

    document_worker = subcommands.add_parser(
        "run-document-download-worker",
        help="Lease and download a bounded batch from an explicitly selected scope set",
    )
    scope_selection = document_worker.add_mutually_exclusive_group(required=True)
    scope_selection.add_argument(
        "--scope-id",
        help="Restrict work to exactly one named scope",
    )
    scope_selection.add_argument(
        "--all-scopes",
        action="store_true",
        help="Explicitly authorize claims across every scope",
    )
    document_worker.add_argument("--limit", type=int, default=25)
    document_worker.add_argument("--lease-seconds", type=int, default=600)
    document_worker.add_argument("--max-attempts", type=int, default=3)
    document_worker.add_argument(
        "--artifact-type",
        action="append",
        choices=("transcript", "presentation", "credit_rating", "recording"),
        default=[],
        dest="artifact_types",
        help="Claim only this artifact type; repeatable and always combined with scope",
    )
    document_worker.add_argument(
        "--throttle-seconds", type=float, default=THROTTLE_SECONDS
    )
    document_worker.add_argument(
        "--max-bytes", type=int, default=DOCUMENT_MAX_RESPONSE_BYTES
    )
    document_worker.add_argument(
        "--timeout-seconds", type=float, default=DOCUMENT_TOTAL_TIMEOUT_SECONDS
    )
    document_worker.add_argument("--output", type=Path)
    document_worker.add_argument("--db", type=Path, default=DB_PATH)

    recovery_report = subcommands.add_parser(
        "document-recovery-report",
        help="Export logical-document failures with a deterministic recovery action",
    )
    recovery_report.add_argument("--scope-id")
    recovery_report.add_argument("--include-pending", action="store_true")
    recovery_report.add_argument("--output", type=Path)
    recovery_report.add_argument("--db", type=Path, default=DB_PATH)

    recovery_requeue = subcommands.add_parser(
        "requeue-document-downloads",
        help="Audit and requeue only explicitly selected settled download jobs",
    )
    recovery_requeue.add_argument(
        "--queue-kind", choices=("generic", "credit_rating"), required=True
    )
    recovery_requeue.add_argument(
        "--job-id", action="append", type=int, required=True, dest="job_ids"
    )
    recovery_requeue.add_argument(
        "--reason-code", choices=RECOVERY_REASON_CODES, required=True
    )
    recovery_requeue.add_argument("--reason-detail")
    recovery_requeue.add_argument("--dry-run", action="store_true")
    recovery_requeue.add_argument("--db", type=Path, default=DB_PATH)

    replacement_url = subcommands.add_parser(
        "register-document-replacement",
        help="Preserve a failed source and queue an audited replacement URL",
    )
    replacement_url.add_argument("--artifact-id", type=int, required=True)
    replacement_url.add_argument("--replaces-source-id", type=int, required=True)
    replacement_url.add_argument("--url", required=True)
    replacement_url.add_argument(
        "--reason-code", choices=RECOVERY_REASON_CODES, required=True
    )
    replacement_url.add_argument("--reason-detail")
    replacement_url.add_argument(
        "--evidence",
        help="Short operator/source note recorded with the replacement provenance",
    )
    replacement_url.add_argument("--dry-run", action="store_true")
    replacement_url.add_argument("--db", type=Path, default=DB_PATH)

    manual_document = subcommands.add_parser(
        "import-manual-document",
        help="Signature-validate and audit an operator-acquired document file",
    )
    manual_document.add_argument("--artifact-id", type=int, required=True)
    manual_document.add_argument("--source-id", type=int, required=True)
    manual_document.add_argument("--file", type=Path, required=True)
    trust = manual_document.add_mutually_exclusive_group(required=True)
    trust.add_argument(
        "--official", action="store_true",
        help="Assert that the bytes are the publisher's original document",
    )
    trust.add_argument(
        "--unverified", action="store_true",
        help="Retain the bytes without asserting official provenance",
    )
    manual_document.add_argument("--reason-detail", required=True)
    manual_document.add_argument("--evidence")
    manual_document.add_argument("--db", type=Path, default=DB_PATH)

    storage_projection = subcommands.add_parser(
        "document-storage-projection",
        help="Measure observed file sizes and project scoped storage with headroom",
    )
    storage_projection.add_argument("--scope-id", required=True)
    storage_projection.add_argument(
        "--planned-target",
        action="append",
        default=[],
        help="Override a planned artifact count as TYPE=COUNT; repeatable",
    )
    storage_projection.add_argument("--headroom-percent", type=float, default=25.0)
    storage_projection.add_argument("--document-store", type=Path, default=DOCUMENT_STORE)
    storage_projection.add_argument("--output", type=Path)
    storage_projection.add_argument("--db", type=Path, default=DB_PATH)

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
    output_targets = tuple(
        value for name in ("output", "output_dir")
        if isinstance((value := getattr(args, name, None)), Path)
    )
    if output_targets:
        assert_configured_writable_targets(
            *output_targets, db_path=getattr(args, "db", DB_PATH)
        )
    if args.command is None or args.command == "shell":
        db_path = getattr(args, "db", DB_PATH)
        return run_interactive_shell(db_path)

    if args.command == "init-db":
        conn = connect(args.db)
        try:
            init_db(conn, apply_migrations=True)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "database": str(args.db.resolve())}))
        return 0

    if args.command == "monitor":
        conn = connect(args.db, read_only=True)
        try:
            init_db(conn)
            run_live_monitor(conn, refresh_interval=args.refresh)
        finally:
            conn.close()
        return 0

    if args.command in {"download-monitor", "monitor-downloads"}:
        conn = connect(args.db, read_only=True)
        try:
            init_db(conn)
            run_download_monitor(conn, refresh_interval=args.refresh)
        finally:
            conn.close()
        return 0

    if args.command == "view":
        try:
            conn = connect(args.db, read_only=True)
            try:
                init_db(conn)
                if args.view_mode == "company":
                    sections = (
                        [s.strip() for s in args.sections.split(",") if s.strip()]
                        if args.sections
                        else None
                    )
                    unknown = set(sections or ()) - set(SECTION_CODES)
                    if unknown:
                        raise ValueError(
                            f"Unknown section(s): {', '.join(sorted(unknown))}. "
                            f"Choose from: {', '.join(SECTION_CODES)}"
                        )
                    output = render_company(
                        conn,
                        args.term,
                        scope=args.scope,
                        periods=args.periods,
                        sections=sections,
                        show_shareholding=not args.no_shareholding,
                    )
                elif args.view_mode == "mf":
                    output = render_mf(
                        conn,
                        publisher=args.publisher,
                        month=args.month,
                        scope=args.scope,
                        isin=args.isin,
                        limit=args.limit,
                    )
                elif args.view_mode == "coverage":
                    output = render_coverage(conn)
                elif args.view_mode == "tables":
                    output = render_tables(conn)
                elif args.view_mode == "monitor":
                    run_live_monitor(conn, refresh_interval=args.refresh)
                    return 0
                elif args.view_mode == "downloads":
                    run_download_monitor(conn, refresh_interval=args.refresh)
                    return 0
                else:
                    output = render_browse(
                        conn, args.table, where=args.where, limit=args.limit
                    )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(output)
        return 0

    if args.command == "quality-report":
        conn = connect(args.db, read_only=True)
        try:
            init_db(conn)
            output = write_quality_report(conn, args.output)
        finally:
            conn.close()
        print(json.dumps({"status": "ok", "output": str(output.resolve())}))
        return 0

    if args.command == "document-quality-report":
        try:
            conn = connect(args.db, read_only=True)
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

    if args.command == "backfill-result-artifacts":
        try:
            payload = backfill_accepted_result_artifacts(
                db_path=args.db, limit=args.limit
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "artifact-coverage":
        try:
            conn = connect(args.db, read_only=True)
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

    if args.command == "screener-requeue-failed":
        try:
            payload = requeue_failed_screener_companies(
                statuses=tuple(args.status or ("failed_terminal",)),
                paths=tuple(args.path or ()),
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "screener-requeue-incomplete-metrics":
        try:
            payload = requeue_incomplete_metric_screener_companies(
                paths=tuple(args.path or ()),
                limit=args.limit,
                dry_run=args.dry_run,
                db_path=args.db,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "screener-discovery-coverage":
        try:
            conn = connect(args.db, read_only=True)
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
                override_validation=args.override_validation,
            )
            if args.command == "exchange-discover-results":
                runner_options["result_period"] = args.result_period
            payload = runner(**runner_options)
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        # A rejected snapshot is stored but changes nothing, and a filing that
        # cannot be resolved to a company is a discovery gap, not a technical
        # failure. Both must be visible in the exit status.
        if (payload.get("validation") or {}).get("status") == "failed":
            return 1
        review = (payload.get("enqueue") or {}).get("manual_review", 0)
        return 1 if review else 0

    if args.command == "exchange-inspect-dataset":
        try:
            payload = inspect_exchange_dataset(
                dataset=args.dataset,
                file=args.file,
                url=args.url,
                result_period=args.result_period,
                sample_rows=args.sample_rows,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, indent=2, sort_keys=True))
        return 0

    if args.command == "exchange-reconciliation-report":
        try:
            conn = connect(args.db, read_only=True)
            try:
                init_db(conn)
                output = write_exchange_reconciliation_report(
                    conn, args.output, args.snapshot_id
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
                    "reconciliation_status": payload["reconciliation_status"],
                    "summary": payload["summary"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        # An open identity conflict blocks automation: this exit code is the gate
        # the scheduler is only turned on behind.
        return 1 if payload["summary"]["open_conflict_count"] else 0

    if args.command == "exchange-scheduler":
        try:
            if args.resume_access:
                if args.status or args.weekly_report or args.catch_up or args.once or args.attempt:
                    raise ValueError("--resume-access cannot be combined with scheduler runs")
                payload = resume_source_access(
                    args.resume_access, args.resume_evidence or "", db_path=args.db
                )
            elif args.resume_evidence:
                raise ValueError("--resume-evidence requires --resume-access")
            elif args.status:
                payload = day_status(args.scheduled_for, db_path=args.db)
            elif args.weekly_report:
                payload = run_weekly_report(db_path=args.db)
            elif args.catch_up:
                payload = catch_up_missed_days(
                    db_path=args.db, max_days=args.catch_up_days
                )
            else:
                payload = run_scheduler(
                    once=args.once, attempt=args.attempt, db_path=args.db
                )
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 1 if payload.get("status") not in (None, "ok") else 0

    if args.command == "exchange-resolve-conflict":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = resolve_identity_conflict(
                    conn,
                    conflict_id=args.conflict_id,
                    resolution=args.resolution,
                    now_utc=utc_now(),
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "exchange-bulk-enqueue":
        from scrapper.commands.exchange_bulk_enqueue import run_bulk_enqueue
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = run_bulk_enqueue(conn, dry_run=args.dry_run)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "screener-period-coverage" or (
        args.command == "exchange-unaccepted-backfill" and args.result_period
    ):
        from .commands.screener_period_coverage import run_period_coverage
        coverage_command = args.command == "screener-period-coverage"
        payload = run_period_coverage(
            period=args.period if coverage_command else args.result_period,
            symbols=args.symbol if coverage_command else (),
            repair=args.repair if coverage_command else not args.dry_run,
            refresh=args.refresh if coverage_command else not args.dry_run,
            limit=args.limit, db_path=args.db,
        )
        print(json.dumps(payload, sort_keys=True))
        return 1 if payload["missing_count"] or payload["refresh_failures"] else 0

    if args.command == "exchange-unaccepted-backfill":
        from scrapper.commands.exchange_unaccepted_backfill import run_unaccepted_backfill
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = run_unaccepted_backfill(conn, dry_run=args.dry_run)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "exchange-universe-coverage":
        try:
            conn = connect(args.db, read_only=True)
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
        conn = connect(args.db, read_only=True)
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
        conn = connect(args.db, read_only=True)
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

    if args.command == "promote-document-links":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = promote_document_links(
                    conn,
                    document_type=args.document_type,
                    dry_run=args.dry_run,
                    limit=args.limit,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "build-document-recovery-targets":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = build_recovery_targets(
                    conn,
                    artifact_type=args.artifact_type,
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "run-document-recovery-worker":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = run_document_recovery_worker(
                    conn,
                    sources=args.sources,
                    limit=args.limit,
                    lease_seconds=args.lease_seconds,
                    throttle_seconds=args.throttle_seconds,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "document-alternate-source-recovery-report":
        try:
            conn = connect(args.db, read_only=True)
            try:
                init_db(conn)
                payload, output = write_alternate_source_recovery_report(
                    conn, output=args.output
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {"status": "ok", **payload["summary"], "output": str(output.resolve())},
                sort_keys=True,
            )
        )
        return 0

    if args.command == "reconcile-promoted-artifacts":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = reconcile_promoted_artifacts(
                    conn, limit=args.limit, dry_run=args.dry_run
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-credit-rating-downloads":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = queue_credit_rating_downloads(conn)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-document-downloads":
        try:
            payload = queue_document_downloads(
                manifest_path=args.manifest,
                db_path=args.db,
                priority=args.priority,
                dry_run=args.dry_run,
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "queue-accepted-document-downloads":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = enqueue_documents_for_all_accepted_parses(conn)
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "run-document-download-worker":
        try:
            selected_scope_id = None if args.all_scopes else args.scope_id
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
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = run_document_download_worker(
                    conn,
                    scope_id=selected_scope_id,
                    artifact_types=tuple(args.artifact_types),
                    limit=args.limit,
                    lease_seconds=args.lease_seconds,
                    max_attempts=args.max_attempts,
                    throttle_seconds=args.throttle_seconds,
                    policy=DownloadPolicy(
                        max_bytes=args.max_bytes,
                        total_timeout_seconds=args.timeout_seconds,
                    ),
                )
                report, output = write_download_queue_report(
                    conn, scope_id=selected_scope_id, output=args.output
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    **payload,
                    "byte_report": report["bytes"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if not payload["outcome_counts"].get("WORKER_EXCEPTION") else 1

    if args.command == "document-recovery-report":
        try:
            conn = connect(args.db, read_only=True)
            try:
                init_db(conn)
                payload, output = write_document_recovery_report(
                    conn,
                    scope_id=args.scope_id,
                    include_pending=args.include_pending,
                    output=args.output,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {"status": "ok", **payload["summary"], "output": str(output.resolve())},
                sort_keys=True,
            )
        )
        return 0

    if args.command == "requeue-document-downloads":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = requeue_document_downloads(
                    conn,
                    queue_kind=args.queue_kind,
                    job_ids=args.job_ids,
                    reason_code=args.reason_code,
                    reason_detail=args.reason_detail,
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "register-document-replacement":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = register_replacement_document_url(
                    conn,
                    artifact_id=args.artifact_id,
                    replaces_artifact_source_id=args.replaces_source_id,
                    replacement_url=args.url,
                    reason_code=args.reason_code,
                    reason_detail=args.reason_detail,
                    evidence={"operator_note": args.evidence} if args.evidence else {},
                    dry_run=args.dry_run,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "import-manual-document":
        try:
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = import_manual_document(
                    conn,
                    artifact_id=args.artifact_id,
                    artifact_source_id=args.source_id,
                    path=args.file,
                    official=args.official,
                    reason_detail=args.reason_detail,
                    evidence={"operator_note": args.evidence} if args.evidence else {},
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(json.dumps({"status": "ok", **payload}, sort_keys=True))
        return 0

    if args.command == "document-storage-projection":
        try:
            planned: dict[str, int] = {}
            for value in args.planned_target:
                artifact_type, separator, count = value.partition("=")
                if not separator or not artifact_type or not count:
                    raise ValueError("--planned-target must use TYPE=COUNT")
                if artifact_type not in ALLOWED_MEDIA_BY_ARTIFACT:
                    raise ValueError(f"Unknown planned artifact type: {artifact_type}")
                planned[artifact_type] = int(count)
            conn = connect(args.db, read_only=True)
            try:
                init_db(conn)
                payload, output = write_document_storage_projection(
                    conn,
                    scope_id=args.scope_id,
                    planned_target_counts=planned,
                    headroom_percent=args.headroom_percent,
                    document_store=args.document_store,
                    output=args.output,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "scope_id": args.scope_id,
                    **payload["actual"],
                    **payload["projection"],
                    "output": str(output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0

    if args.command == "run-credit-rating-download-worker":
        try:
            if args.max_bytes > DOCUMENT_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"--max-bytes cannot exceed configured bound {DOCUMENT_MAX_RESPONSE_BYTES}"
                )
            if args.timeout_seconds > DOCUMENT_TOTAL_TIMEOUT_SECONDS:
                raise ValueError(
                    f"--timeout-seconds cannot exceed configured bound "
                    f"{DOCUMENT_TOTAL_TIMEOUT_SECONDS}"
                )
            conn = connect(args.db)
            try:
                init_db(conn)
                payload = run_credit_rating_download_worker(
                    conn,
                    limit=args.limit,
                    policy=DownloadPolicy(
                        max_bytes=args.max_bytes,
                        total_timeout_seconds=args.timeout_seconds,
                    ),
                    allowed_hosts=args.host,
                    throttle_seconds=args.throttle_seconds,
                )
            finally:
                conn.close()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
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
        conn = connect(args.db, read_only=not args.persist)
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
