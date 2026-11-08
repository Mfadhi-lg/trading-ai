"""Compatible CLI facade; registration is inert and handlers load on dispatch."""

from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import sys
from pathlib import Path
from ..config import (
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
from ..cli_groups.registration import build_parser as _parser
from ..cli_groups.dispatch import lazy_handler, load_attribute
from ..cli_groups.exchange import COMMANDS as _EXCHANGE_COMMANDS, dispatch_exchange
from ..cli_groups.screener import COMMANDS as _SCREENER_COMMANDS, dispatch_screener
from ..cli_groups.documents import COMMANDS as _DOCUMENT_COMMANDS, dispatch_documents
from ..cli_groups.mutual_funds import COMMANDS as _MF_COMMANDS, dispatch_mutual_funds
from ..cli_groups.operations import COMMANDS as _OPERATIONS_COMMANDS, dispatch_operations

connect = lazy_handler('.db.database', 'connect')
init_db = lazy_handler('.db.database', 'init_db')
build_recovery_targets = lazy_handler('.db.document_recovery_repo', 'build_recovery_targets')
derive_stock_month_features_through = lazy_handler('.db.mutual_funds.features', 'derive_stock_month_features_through')
resolve_identity_conflict = lazy_handler('.db.exchange_repo', 'resolve_identity_conflict')
utc_now = lazy_handler('.db.repo', 'utc_now')
write_mf_feature_export = lazy_handler('.exports.mf_feature_json_export', 'write_mf_feature_export')
write_mf_coverage_report = lazy_handler('.exports.mf_coverage_report', 'write_mf_coverage_report')
write_quality_report = lazy_handler('.exports.quality_report', 'write_quality_report')
write_document_quality_report = lazy_handler('.exports.document_quality_report', 'write_document_quality_report')
write_artifact_coverage_report = lazy_handler('.exports.artifact_coverage', 'write_artifact_coverage_report')
write_screener_discovery_coverage = lazy_handler('.exports.screener_discovery_coverage', 'write_screener_discovery_coverage')
write_exchange_reconciliation_report = lazy_handler('.exports.exchange_reconciliation_report', 'write_exchange_reconciliation_report')
write_exchange_universe_coverage = lazy_handler('.exports.exchange_universe_coverage', 'write_exchange_universe_coverage')
discover_exchange_results = lazy_handler('.workflows.exchange.universe', 'discover_exchange_results')
inspect_exchange_dataset = lazy_handler('.workflows.exchange.universe', 'inspect_exchange_dataset')
refresh_exchange_universe = lazy_handler('.workflows.exchange.universe', 'refresh_exchange_universe')
catch_up_missed_days = lazy_handler('.workflows.exchange.schedule', 'catch_up_missed_days')
day_status = lazy_handler('.workflows.exchange.schedule', 'day_status')
run_scheduler = lazy_handler('.operations.coordinator.exchange', 'run_scheduler')
run_weekly_report = lazy_handler('.workflows.exchange.schedule', 'run_weekly_report')
scrape_screener_company = lazy_handler('.workflows.screener.ingest', 'scrape_screener_company')
reprocess_screener_blobs = lazy_handler('.workflows.screener.ingest', 'reprocess_screener_blobs')
discover_screener_results = lazy_handler('.pipeline.screener_discovery', 'discover_screener_results')
requeue_failed_screener_companies = lazy_handler('.pipeline.screener_discovery', 'requeue_failed_screener_companies')
requeue_incomplete_metric_screener_companies = lazy_handler('.pipeline.screener_discovery', 'requeue_incomplete_metric_screener_companies')
run_screener_company_worker = lazy_handler('.workflows.screener.company_worker', 'run_screener_company_worker')
render_browse = lazy_handler('.view', 'render_browse')
render_company = lazy_handler('.view', 'render_company')
render_coverage = lazy_handler('.view', 'render_coverage')
render_mf = lazy_handler('.view', 'render_mf')
render_tables = lazy_handler('.view', 'render_tables')
run_download_monitor = lazy_handler('.view', 'run_download_monitor')
run_live_monitor = lazy_handler('.view', 'run_live_monitor')
run_interactive_shell = lazy_handler('.view', 'run_interactive_shell')
scrape_trendlyne_company = lazy_handler('.workflows.mutual_funds.trendlyne_vertical', 'scrape_trendlyne_company')
build_trendlyne_pilot_plan = lazy_handler('.workflows.mutual_funds.trendlyne_pilot', 'build_trendlyne_pilot_plan')
register_trendlyne_pilot_plan = lazy_handler('.workflows.mutual_funds.trendlyne_pilot', 'register_trendlyne_pilot_plan')
run_trendlyne_pilot_batch = lazy_handler('.workflows.mutual_funds.trendlyne_pilot', 'run_trendlyne_pilot_batch')
execute_import = lazy_handler('.workflows.mutual_funds.import_mf', 'execute_import')
import_plan_payload = lazy_handler('.workflows.mutual_funds.import_mf', 'import_plan_payload')
plan_directory_import = lazy_handler('.workflows.mutual_funds.import_mf', 'plan_directory_import')
plan_import = lazy_handler('.workflows.mutual_funds.import_mf', 'plan_import')
execute_mfdata_crosscheck = lazy_handler('.workflows.mutual_funds.crosscheck_mfdata', 'execute_mfdata_crosscheck')
discover_documents = lazy_handler('.workflows.documents.discovery', 'discover_documents')
fetch_documents = lazy_handler('.pipeline.fetch_documents', 'fetch_documents')
promote_document_links = lazy_handler('.documents.discovery.promotion', 'promote_document_links')
queue_credit_rating_downloads = lazy_handler('.pipeline.credit_rating_downloads', 'queue_credit_rating_downloads')
run_credit_rating_download_worker = lazy_handler('.pipeline.credit_rating_downloads', 'run_credit_rating_download_worker')
enqueue_documents_for_all_accepted_parses = lazy_handler('.pipeline.accepted_document_downloads', 'enqueue_documents_for_all_accepted_parses')
queue_document_downloads = lazy_handler('.pipeline.queue_document_downloads', 'queue_document_downloads')
run_document_download_worker = lazy_handler('.pipeline.run_document_download_worker', 'run_document_download_worker')
write_download_queue_report = lazy_handler('.pipeline.run_document_download_worker', 'write_download_queue_report')
import_manual_document = lazy_handler('.documents.recovery.failed_download', 'import_manual_document')
register_replacement_document_url = lazy_handler('.documents.recovery.failed_download', 'register_replacement_document_url')
requeue_document_downloads = lazy_handler('.documents.recovery.failed_download', 'requeue_document_downloads')
write_document_recovery_report = lazy_handler('.pipeline.document_download_recovery', 'write_document_recovery_report')
run_document_recovery_worker = lazy_handler('.documents.recovery.alternate_sources', 'run_document_recovery_worker')
write_document_storage_projection = lazy_handler('.exports.document_storage_projection', 'write_document_storage_projection')
write_alternate_source_recovery_report = lazy_handler('.exports.document_alternate_source_recovery', 'write_alternate_source_recovery_report')
extract_document = lazy_handler('.pipeline.extract_document', 'extract_document')
normalize_credit_rating_document = lazy_handler('.pipeline.normalize_credit_rating', 'normalize_credit_rating_document')
ingest_official_transcript = lazy_handler('.pipeline.ingest_official_transcript', 'ingest_official_transcript')
normalize_recording = lazy_handler('.pipeline.normalize_recording', 'normalize_recording')
transcribe_recording = lazy_handler('.pipeline.transcribe_recording', 'transcribe_recording')
reconcile_artifacts = lazy_handler('.pipeline.reconcile_artifacts', 'reconcile_artifacts')
reconcile_promoted_artifacts = lazy_handler('.pipeline.reconcile_promoted_artifacts', 'reconcile_promoted_artifacts')
backfill_accepted_result_artifacts = lazy_handler('.pipeline.result_artifacts', 'backfill_accepted_result_artifacts')

_CONSTANT_OWNERS = {
    'FEATURE_SCOPES': ('.db.mutual_funds.features', 'FEATURE_SCOPES'),
    'CONFLICT_RESOLUTIONS': ('.db.exchange_repo', 'CONFLICT_RESOLUTIONS'),
    'RESULT_FILING_DATASETS': ('.workflows.exchange.universe', 'RESULT_FILING_DATASETS'),
    'UNIVERSE_DATASETS': ('.workflows.exchange.universe', 'UNIVERSE_DATASETS'),
    'DATASET_KEYS': ('.sources.exchange.datasets', 'DATASET_KEYS'),
    'REQUEUEABLE_STATUSES': ('.db.screener_discovery_repo', 'REQUEUEABLE_STATUSES'),
    'SECTION_CODES': ('.view.render', 'SECTION_CODES'),
    'PILOT_SYMBOLS': ('.workflows.mutual_funds.trendlyne_pilot', 'PILOT_SYMBOLS'),
    'PROMOTABLE': ('.documents.discovery.promotion', 'PROMOTABLE'),
    'RECOVERY_REASON_CODES': ('.pipeline.document_download_recovery', 'RECOVERY_REASON_CODES'),
    'ALLOWED_MEDIA_BY_ARTIFACT': ('.documents.downloader', 'ALLOWED_MEDIA_BY_ARTIFACT'),
    'DownloadPolicy': ('.documents.downloader', 'DownloadPolicy'),
}

def __getattr__(name):
    if name in _CONSTANT_OWNERS:
        return load_attribute(*_CONSTANT_OWNERS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> int:
    return _dispatch(_parser().parse_args(argv))


def _dispatch(args) -> int:
    if args.command == "run-acquisition-role":
        # Identity refusal precedes signal ownership or any worker startup.
        assert_configured_writable_targets(db_path=args.db)
        from ..operations.workers.roles import run_role
        run_role(args.role, db_path=args.db)
        return 0
    output_targets = tuple(
        value for name in ("output", "output_dir")
        if isinstance((value := getattr(args, name, None)), Path)
    )
    if output_targets:
        assert_configured_writable_targets(
            *output_targets, db_path=getattr(args, "db", DB_PATH)
        )
    if args.command in _EXCHANGE_COMMANDS:
        return dispatch_exchange(args, sys.modules[__name__])

    if args.command in _SCREENER_COMMANDS or (
        args.command == "scrape-one" and args.source == "screener"
    ):
        return dispatch_screener(args, sys.modules[__name__])

    if args.command in _DOCUMENT_COMMANDS:
        return dispatch_documents(args, sys.modules[__name__])

    if args.command in _MF_COMMANDS:
        return dispatch_mutual_funds(args, sys.modules[__name__])

    if args.command is None or args.command in _OPERATIONS_COMMANDS:
        return dispatch_operations(args, sys.modules[__name__])

    return dispatch_mutual_funds(args, sys.modules[__name__])
