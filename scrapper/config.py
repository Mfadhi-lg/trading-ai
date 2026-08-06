"""Central configuration: paths, network settings, and parser versions."""
import os
from pathlib import Path

# --- paths ---
ROOT = Path(__file__).resolve().parent
STORE = Path(os.environ.get("SCRAPER_STORE_PATH", str(ROOT / "store")))
FILES = STORE / "files"
SNAPSHOTS = STORE / "snapshots"
EXPORTS = STORE / "exports"
DB_PATH = Path(os.environ.get("SCRAPER_DB_PATH", str(STORE / "scraper.db")))
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
DOCUMENT_CONTENT_SNAPSHOTS = SNAPSHOTS / "artifact_content"
DOCUMENT_DOWNLOAD_EXPORTS = EXPORTS / "artifact_download"
DOCUMENT_EXTRACTION_EXPORTS = EXPORTS / "document_extraction"
DOCUMENT_CREDIT_RATING_EXPORTS = EXPORTS / "credit_rating"
DOCUMENT_TRANSCRIPT_EXPORTS = EXPORTS / "official_transcript"
DOCUMENT_RECORDING_EXPORTS = EXPORTS / "recording_audio"
DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS = EXPORTS / "machine_transcript"
DOCUMENT_QUALITY_EXPORTS = EXPORTS / "document_quality"
DOCUMENT_RECONCILIATION_EXPORTS = EXPORTS / "artifact_reconciliation"
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
PREFER_CONSOLIDATED = True    # try /consolidated/ first, fall back to standalone
PARSER_NAME = "screener-company"
PARSER_VERSION = "1.0.0"
PEER_PARSER_NAME = "screener-peer-table"
PEER_PARSER_VERSION = "0.9.0"
RESULT_SCREEN_PARSER_NAME = "screener-result-screen"
RESULT_SCREEN_PARSER_VERSION = "1.0.1"
MANIFEST_VERSION = "1.3.0"
TRENDLYNE_PARSER_NAME = "trendlyne-monthly-mf-page"
TRENDLYNE_PARSER_VERSION = "0.5.0"
TRENDLYNE_MANIFEST_VERSION = "0.5.0"

# --- screener ---
SCREENER_BASE = "https://www.screener.in"
TRENDLYNE_BASE = "https://trendlyne.com"


def ensure_dirs() -> None:
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
    DOCUMENT_CONTENT_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_DOWNLOAD_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_EXTRACTION_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_CREDIT_RATING_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_TRANSCRIPT_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECORDING_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_MACHINE_TRANSCRIPT_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_QUALITY_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_RECONCILIATION_EXPORTS.mkdir(parents=True, exist_ok=True)
    DOCUMENT_ARTIFACT_COVERAGE_EXPORTS.mkdir(parents=True, exist_ok=True)
    for path in (
        MF_IMPORT_INBOX,
        MF_IMPORT_ACCEPTED,
        MF_IMPORT_REJECTED,
        MF_IMPORT_QUARANTINE,
    ):
        path.mkdir(parents=True, exist_ok=True)
