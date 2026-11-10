"""Inert argparse registration preserving the existing operator contract.

Choice literals describe CLI syntax only. Contract tests compare them to their
existing service owners; dispatch continues to use those owners for policy.
"""

import argparse
from pathlib import Path
from ..config import (
    DB_PATH,
    DOCUMENT_STORE,
    DOCUMENT_MAX_RESPONSE_BYTES,
    DOCUMENT_TOTAL_TIMEOUT_SECONDS,
    SCREENER_WORKER_LIMIT,
    EXPORTS,
    THROTTLE_SECONDS,
)

FEATURE_SCOPES = ('all_scheme', 'active_equity')
CONFLICT_RESOLUTIONS = ('accepted_official', 'kept_existing', 'dismissed')
RESULT_FILING_DATASETS = ('nse_financial_results', 'bse_financial_results')
UNIVERSE_DATASETS = ('nse_equity_master', 'nse_sme_master', 'bse_security_master', 'nse_mii_security_file', 'nse_symbol_change', 'nse_name_change')
DATASET_KEYS = ('nse_equity_master', 'nse_sme_master', 'nse_mii_security_file', 'bse_security_master', 'nse_symbol_change', 'nse_name_change', 'nse_financial_results', 'bse_financial_results')
REQUEUEABLE_STATUSES = ('failed_retryable', 'failed_terminal', 'manual_review')
SECTION_CODES = ('quarters', 'pnl', 'balance_sheet', 'cash_flow', 'ratios')
PROMOTABLE = ('concall_transcript', 'concall_ppt', 'concall_recording', 'credit_rating')
RECOVERY_REASON_CODES = ('TRANSIENT_SOURCE_FAILURE', 'SOURCE_REPUBLISHED', 'RESOLVER_UPDATED', 'OPERATOR_VERIFIED', 'DISCOVERY_REFRESH')
ALLOWED_MEDIA_BY_ARTIFACT = ('presentation', 'credit_rating', 'transcript', 'recording')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scraper")
    subcommands = parser.add_subparsers(dest="command", required=False)

    role = subcommands.add_parser("run-acquisition-role", help="Serve a fixed bounded acquisition role")
    role.add_argument("--role", required=True, choices=(
        "document-download-worker", "rating-recovery-download",
        "credit-rating-worker-care", "credit-rating-worker-india-ratings",
        "credit-rating-worker-other"))
    role.add_argument("--db", type=Path, default=DB_PATH)

    shell = subcommands.add_parser(
        "shell",
        help="Interactive CLI shell session with slash commands (Claude Code style)",
    )
    shell.add_argument("--db", type=Path, default=DB_PATH)

    init = subcommands.add_parser("init-db", help="Explicitly apply the selected backend's schema migrations")
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

    financial_coverage = subcommands.add_parser(
        "financial-coverage-audit",
        help="Export read-only historical financial statement and metric coverage",
    )
    financial_coverage.add_argument(
        "--symbol", action="append", default=[],
        help="Limit to an NSE symbol; repeat for multiple companies",
    )
    financial_coverage.add_argument(
        "--output", type=Path,
        default=EXPORTS / "financial-coverage-audit.json",
        help="JSON output path",
    )
    financial_coverage.add_argument(
        "--csv-output", type=Path,
        default=EXPORTS / "financial-coverage-audit.csv",
        help="CSV output path",
    )
    financial_coverage.add_argument("--db", type=Path, default=DB_PATH)

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
    rating_selection = rating_worker.add_mutually_exclusive_group()
    rating_selection.add_argument("--rating-instance", choices=("care", "india-ratings", "other"),
        help="Run a configured host partition with mandatory shared permits")
    rating_selection.add_argument(
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
    scope_selection.add_argument(
        "--eligible-scope", action="append", default=[], dest="eligible_scopes",
        choices=("accepted-screener-concall", "da06-rating-recovery"),
        help="Common generic executor allowlist; repeat for weighted 3:1 fairness",
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
    reference = subcommands.add_parser(
        "exchange-import-reference", help="Explicitly import a validated reference CSV into an empty exchange table",
    )
    reference.add_argument("--file", type=Path)
    reference.add_argument("--db", type=Path, default=DB_PATH)
    return parser
