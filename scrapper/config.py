"""Central configuration: paths, network settings, and parser versions."""
import os
from pathlib import Path

# --- paths ---
ROOT = Path(__file__).resolve().parent
STORE = Path(os.environ.get("SCRAPER_STORE_PATH", str(ROOT / "store")))
RUNTIME_ROOT = STORE  # existing STORE imports and SCRAPER_STORE_PATH remain valid
FILES = Path(os.environ.get("SCRAPER_FILES_PATH", str(STORE / "files")))
SNAPSHOTS = Path(os.environ.get("SCRAPER_SNAPSHOTS_PATH", str(STORE / "snapshots")))
EXPORTS = Path(os.environ.get("SCRAPER_EXPORTS_PATH", str(STORE / "exports")))
DB_PATH = Path(os.environ.get("SCRAPER_DB_PATH", str(STORE / "scraper.db")))
DATABASE_DIR = DB_PATH.parent  # DB/WAL stay at the existing location by default
DOCUMENT_STORE = Path(
    os.environ.get("SCRAPER_DOCUMENT_STORE_PATH", str(SNAPSHOTS))
)
DOCUMENTS = DOCUMENT_STORE  # immutable-store alias; no content-path remapping
# These locations describe ownership. Their consumers are selected separately;
# ensure_dirs does not eagerly create campaign, log, browser or backup roots.
CAMPAIGNS = Path(os.environ.get("SCRAPER_CAMPAIGNS_PATH", str(STORE / "campaigns")))
LOGS = Path(os.environ.get("SCRAPER_LOGS_PATH", str(STORE / "logs")))
BROWSER_STATE = Path(os.environ.get("SCRAPER_BROWSER_STATE_PATH", str(STORE / "browser-state")))
BACKUPS = Path(os.environ.get("SCRAPER_BACKUPS_PATH", str(STORE / "backups")))
MIGRATIONS_PATH = ROOT / "db" / "migrations"
MF_IMPORT = STORE / "mf_import"
MF_IMPORT_INBOX = MF_IMPORT / "inbox"
MF_IMPORT_ACCEPTED = MF_IMPORT / "accepted"
MF_IMPORT_REJECTED = MF_IMPORT / "rejected"
MF_IMPORT_QUARANTINE = MF_IMPORT / "quarantine"
AMC_WORKBOOK_SNAPSHOTS = SNAPSHOTS / "amc_workbook"
MF_WORKBOOK_EXPORTS = EXPORTS / "mf_workbook"
MF_FEATURE_EXPORTS = EXPORTS / "mf_features"
MF_COVERAGE_EXPORTS = EXPORTS / "mf_coverage"
MFDATA_SNAPSHOTS = SNAPSHOTS / "mfdata"
MFDATA_EXPORTS = EXPORTS / "mfdata_crosscheck"
DOCUMENT_DISCOVERY_SNAPSHOTS = SNAPSHOTS / "artifact_discovery"
DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS = DOCUMENT_DISCOVERY_SNAPSHOTS / "recovery"
DOCUMENT_CONTENT_SNAPSHOTS = DOCUMENT_STORE / "artifact_content"
DOCUMENT_DOWNLOAD_EXPORTS = EXPORTS / "artifact_download"
DOCUMENT_EXTRACTION_EXPORTS = EXPORTS / "document_extraction"
DOCUMENT_CREDIT_RATING_EXPORTS = EXPORTS / "credit_rating"
DOCUMENT_TRANSCRIPT_EXPORTS = EXPORTS / "official_transcript"
DOCUMENT_RECORDING_EXPORTS = EXPORTS / "recording_audio"
DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS = EXPORTS / "machine_transcript"
DOCUMENT_QUALITY_EXPORTS = EXPORTS / "document_quality"
DOCUMENT_RECONCILIATION_EXPORTS = EXPORTS / "artifact_reconciliation"
DOCUMENT_RECOVERY_EXPORTS = EXPORTS / "document_recovery"
DOCUMENT_ARTIFACT_COVERAGE_EXPORTS = EXPORTS / "artifact_coverage"

# Workbook safety limits are checked before any package XML is parsed. The PPFAS
# source serves OOXML bytes under a misleading .xls filename; its versioned M02
# manifest permits that exact format after the normal package safety checks.
# Genuine legacy binary XLS containers remain inventoried but unparsed.
MF_MAX_FILE_BYTES = 50 * 1024 * 1024
MF_MAX_ZIP_ENTRIES = 10_000
MF_MAX_ZIP_MEMBER_BYTES = 100 * 1024 * 1024
MF_MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MF_MAX_ZIP_COMPRESSION_RATIO = 100
MF_MAX_XML_MEMBER_BYTES = 10 * 1024 * 1024
MFDATA_MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# D02 streams responses and stops as soon as either bound is crossed. The CLI
# may lower these limits for a run, but never raises them above these defaults
# implicitly.
DOCUMENT_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
DOCUMENT_TOTAL_TIMEOUT_SECONDS = 120.0
DOCUMENT_CONNECT_TIMEOUT_SECONDS = 10.0
DOCUMENT_READ_TIMEOUT_SECONDS = 30.0
DOCUMENT_MAX_REDIRECTS = 5
DOCUMENT_DOWNLOAD_CHUNK_BYTES = 64 * 1024
DOCUMENT_NORMALIZED_AUDIO_MAX_BYTES = 512 * 1024 * 1024
DOCUMENT_MEDIA_PROCESS_TIMEOUT_SECONDS = 300.0
DOCUMENT_STORE_SENTINEL = ".trading_ai_document_store"
DOCUMENT_REQUIRE_SENTINEL = os.environ.get(
    "DOCUMENT_REQUIRE_SENTINEL",
    "1" if "SCRAPER_DOCUMENT_STORE_PATH" in os.environ else "0",
).strip().lower() not in {"0", "false", "no"}
DOCUMENT_MIN_FREE_BYTES = int(
    os.environ.get("DOCUMENT_MIN_FREE_BYTES", str(100 * 1024 * 1024 * 1024))
)
DOCUMENT_MIN_FREE_PERCENT = float(
    os.environ.get("DOCUMENT_MIN_FREE_PERCENT", "15")
)
# The reserve above is only as trustworthy as the statvfs behind it, and a bind
# mount can answer for a filesystem that holds none of these bytes: Docker
# Desktop reports the macOS root volume for a /Volumes mount. Capacity alone
# does not detect it — the root volume and the external disk here differ by
# under 1% in total size while differing by 320 GiB in free space — so the host,
# which can measure the real device, publishes its reading into the store root
# and the guard prefers that file over its own statvfs.
DOCUMENT_STORE_CAPACITY_FILE = ".trading_ai_document_store_capacity.json"
# A stale reading is a wrong reading: bytes land continuously, so a file that
# stopped being refreshed must fail closed rather than authorize writes.
DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS = int(
    os.environ.get("DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS", "3600")
)
# On a host whose statvfs is trustworthy the file is unnecessary. Where it is
# not, requiring it is what converts a silent over-report into a refusal.
DOCUMENT_STORE_REQUIRE_CAPACITY_FILE = os.environ.get(
    "DOCUMENT_STORE_REQUIRE_CAPACITY_FILE", "0"
).strip().lower() not in {"0", "false", "no"}
DOCUMENT_RECOVERY_DATE_WINDOW_DAYS = int(
    os.environ.get("DOCUMENT_RECOVERY_DATE_WINDOW_DAYS", "21")
)
DOCUMENT_RECOVERY_MAX_DATE_WINDOW_DAYS = 62

# --- exchange snapshot guard (C1/C2) ---
# A dated exchange file is judged as a whole before it may rewrite current
# state. The row floor is the one threshold that is genuinely per-dataset — it
# encodes how large the published universe is known to be, so a truncated
# publication is rejected on its size alone — and it therefore lives with the
# rest of the dataset's definition in `sources/exchange/datasets.py`. This value
# is only the fallback for a dataset the registry does not describe.
EXCHANGE_GUARD_ROW_FLOOR = 50
EXCHANGE_GUARD_ROW_COUNT_DROP_RATIO = 0.10
EXCHANGE_GUARD_REJECTED_ROW_RATE = 0.05
EXCHANGE_GUARD_DUPLICATE_ISIN_RATE = 0.01
EXCHANGE_GUARD_ABSENCE_VOLUME_FLOOR = 100
EXCHANGE_GUARD_ABSENCE_VOLUME_RATIO = 0.05

# Absence from one master is not a delisting. A security must be missing from
# this many distinct effective dates before it stops being present.
EXCHANGE_ABSENCE_CONFIRMATIONS = 3

# --- exchange scheduler (C7) ---
# Three attempts per scheduled day, in IST. The exchanges publish their end-of-day
# files during the evening; a dataset that is simply not published yet is retried
# rather than treated as a failure, and only the last attempt is final.
EXCHANGE_SCHEDULE_ATTEMPTS = (("19:30", 0), ("21:00", 0), ("06:00", 1))
EXCHANGE_REPORT_DAY = "sun"
EXCHANGE_REPORT_TIME = "08:00"
EXCHANGE_SCHEDULE_EXPORTS = EXPORTS / "exchange_schedule"

# The rung on which a period-addressed result feed is also asked for the
# previous quarter. Late, corrected and restated filings keep arriving after a
# quarter closes, so the feed for it is re-read once a day rather than abandoned
# the moment the next quarter opens.
EXCHANGE_RESULT_REVISION_ATTEMPT = 1

# --- auth ---
# A file holding the Screener `sessionid` cookie (raw value or "sessionid=...").
# Used ONLY to unlock the custom ratio panel. Never committed, never printed.
SCREENER_COOKIE_FILE = Path(
    os.environ.get("SCREENER_SESSION_FILE", str(Path.home() / ".screener_session"))
)

# --- network / politeness ---
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
THROTTLE_SECONDS = 4.0        # base delay between live fetches
THROTTLE_JITTER = 1.5         # + random 0..jitter
REQUEST_TIMEOUT = 30
SCREENER_THROTTLE_SECONDS = float(
    os.environ.get("SCREENER_THROTTLE_SECONDS", str(THROTTLE_SECONDS))
)
SCREENER_THROTTLE_JITTER = float(
    os.environ.get("SCREENER_THROTTLE_JITTER", str(THROTTLE_JITTER))
)
SCREENER_WORKER_LIMIT = int(os.environ.get("SCREENER_WORKER_LIMIT", "5"))
# Companies below this market-cap floor are outside the initial research scope.
# The value is in the same INR crore unit exposed by Screener's Market Cap metric.
SCREENER_MIN_MARKET_CAP_CRORE = float(
    os.environ.get("SCREENER_MIN_MARKET_CAP_CRORE", "580")
)
# Financial ingestion is standalone-only.  This name remains temporarily for
# callers importing it, but its value must never select a consolidated URL.
PREFER_CONSOLIDATED = False
PARSER_NAME = "screener-company"
PARSER_VERSION = "1.1.2"
PEER_PARSER_NAME = "screener-peer-table"
PEER_PARSER_VERSION = "0.9.0"
RESULT_SCREEN_PARSER_NAME = "screener-result-screen"
RESULT_SCREEN_PARSER_VERSION = "1.0.1"
MANIFEST_VERSION = "1.4.1"
TRENDLYNE_PARSER_NAME = "trendlyne-monthly-mf-page"
TRENDLYNE_PARSER_VERSION = "0.5.0"
TRENDLYNE_MANIFEST_VERSION = "0.5.0"

# --- screener ---
SCREENER_BASE = "https://www.screener.in"
TRENDLYNE_BASE = "https://trendlyne.com"


def ensure_dirs() -> None:
    assert_configured_writable_targets()
    STORE.mkdir(parents=True, exist_ok=True)
    FILES.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    AMC_WORKBOOK_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    MF_WORKBOOK_EXPORTS.mkdir(parents=True, exist_ok=True)
    MF_FEATURE_EXPORTS.mkdir(parents=True, exist_ok=True)
    MF_COVERAGE_EXPORTS.mkdir(parents=True, exist_ok=True)
    MFDATA_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    MFDATA_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_DISCOVERY_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECOVERY_DISCOVERY_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    # When an external document store is configured, its root and sentinel are
    # an authorization boundary.  Do not create even an empty content
    # directory there until ``assert_document_store_ready`` has passed.  The
    # D02 persistence primitive creates the digest parent after that check.
    if not DOCUMENT_REQUIRE_SENTINEL:
        DOCUMENT_CONTENT_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_DOWNLOAD_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_EXTRACTION_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_CREDIT_RATING_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_TRANSCRIPT_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECORDING_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_QUALITY_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECONCILIATION_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECOVERY_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_ARTIFACT_COVERAGE_EXPORTS.mkdir(parents=True, exist_ok=True)
    EXCHANGE_SCHEDULE_EXPORTS.mkdir(parents=True, exist_ok=True)
    for path in (
        MF_IMPORT_INBOX,
        MF_IMPORT_ACCEPTED,
        MF_IMPORT_REJECTED,
        MF_IMPORT_QUARANTINE,
    ):
        path.mkdir(parents=True, exist_ok=True)


def assert_configured_writable_targets(
    *extra: Path,
    db_path: Path = DB_PATH,
    document_path: Path = DOCUMENT_STORE,
) -> None:
    """Authorize configured writes; the delayed import keeps config import inert."""
    from .runtime_identity import assert_writable_targets

    assert_writable_targets(
        db=db_path, store=STORE, documents=document_path,
        extra=(FILES, SNAPSHOTS, EXPORTS, CAMPAIGNS, LOGS, BROWSER_STATE, BACKUPS, *extra),
    )
