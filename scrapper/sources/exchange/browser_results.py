"""Retained BSE browser response transport for canonical exchange ingestion.

No browser launch, HTTP request, database write or access-hold mutation here.
This adapter supplies the original FetchResponse to discover_exchange_results;
all parsing, guards, filing ownership and queue decisions remain canonical.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ...core.http import FetchResponse
from .result_filings import BSE_FINANCIAL_RESULTS, parse_result_filings
from .transport import validate_exchange_dataset_url


@dataclass(frozen=True)
class RetainedBSEBrowserResults:
    response: FetchResponse
    result_period: str
    captured_at_utc: str
    content_sha256: str

    @classmethod
    def load(cls, *, receipt_path: Path, raw_path: Path,
             verification_path: Path) -> "RetainedBSEBrowserResults":
        receipt = json.loads(Path(receipt_path).read_text())
        verification = json.loads(Path(verification_path).read_text())
        raw = Path(raw_path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != verification["raw_sha256"] or len(raw) != verification["raw_bytes"]:
            raise ValueError("Retained browser response hash/size differs from live evidence")
        metadata = receipt["response"]
        if metadata != verification["response"]:
            raise ValueError("Browser receipt/source identity differs from live evidence")
        for url in (metadata["requested_url"], metadata["final_url"]):
            validate_exchange_dataset_url(url, "BSE")
            parts = urlsplit(url)
            if (parts.hostname != "api.bseindia.com" or
                    parts.path != "/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w"):
                raise ValueError("Retained browser source is not the BSE integrated-results service")
        if (metadata["status_code"] != 200 or metadata["fetch_status"] != "ok" or
                metadata["auth_mode"] != "public_browser" or
                receipt["status"] != "PARSED_PARTIAL_SURFACE" or
                verification["browser_result"] != receipt["status"]):
            raise ValueError("Browser evidence is not a successful public-browser response")
        captured = datetime.fromisoformat(verification["finished_at_utc"])
        started = datetime.fromisoformat(verification["started_at_utc"])
        if captured.tzinfo is None or started.tzinfo is None or captured < started:
            raise ValueError("Browser capture interval must contain ordered aware timestamps")
        local = started.astimezone(ZoneInfo("Asia/Kolkata"))
        slot = "morning" if local.hour < 12 else "evening"
        if receipt["day_ist"] != local.date().isoformat() or receipt["slot_ist"] != slot:
            raise ValueError("Browser receipt IST date/slot differs from capture time")
        period = receipt["result_period"]
        if period != verification["result_period"]:
            raise ValueError("Browser receipt period differs from live verification")
        parsed = parse_result_filings(raw, BSE_FINANCIAL_RESULTS, result_period=period)
        if (parsed.rejected or not parsed.filings or
                parsed.accepted_row_count != receipt["accepted_row_count"]):
            raise ValueError("Retained browser response no longer satisfies the recorded parser contract")
        if any(datetime.fromisoformat(f.broadcast_at_utc) > captured for f in parsed.filings):
            raise ValueError("Browser filing publication is after the capture interval")
        return cls(FetchResponse(content=raw, **metadata), period,
                   captured.isoformat(), digest)

    def __call__(self, url: str, exchange: str) -> FetchResponse:
        if exchange != "BSE" or url != self.response.requested_url:
            raise ValueError("Retained browser fetcher only serves its exact recorded BSE URL")
        return self.response


def preview_retained_bse_results(source: RetainedBSEBrowserResults, *, db_path: Path):
    """Read-only canonical validation/queue preview, with capture provenance."""
    from ...workflows.exchange.universe import discover_exchange_results
    local = datetime.fromisoformat(source.captured_at_utc).astimezone(ZoneInfo("Asia/Kolkata"))
    payload = discover_exchange_results(
        dataset=BSE_FINANCIAL_RESULTS, url=source.response.requested_url,
        result_period=source.result_period, effective_date=local.date().isoformat(),
        now_utc=lambda: source.captured_at_utc, fetcher=source, db_path=db_path,
        dry_run=True,
    )
    return payload | {"browser_capture_at_utc": source.captured_at_utc,
                      "browser_content_sha256": source.content_sha256,
                      "coverage": "public application default window; not complete historical coverage"}
