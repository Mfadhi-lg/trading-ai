"""Bounded, read-only BSE browser candidate; deliberately not scheduler-wired.

Publisher consent is required. Uses installed visible Chrome, a fresh
context and one public page only. No cookies imported, retry, stealth,
proxy, database, hold mutation or company crawl. A successful page alone is not
a successful dataset: scope-bearing bytes must pass the existing parser.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import re
import time

from ..core.access_policy import refuse_blocked_terms_target
from ..core.http import FetchResponse
from ..sources.exchange.result_filings import (
    BSE_FINANCIAL_RESULTS, ParsedResultFilings, parse_result_filings,
)
from ..sources.exchange.transport import validate_exchange_dataset_url

PAGE_URL = "https://www.bseindia.com/corporates/Comp_ResultsNew"
DOWNLOAD_URL = "https://api.bseindia.com/BseIndiaAPI/api/NSTodayResults_Download/w"
RESULT_PATH = "/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w"


def _validate_period(value):
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("result_period must be YYYY-MM")


@dataclass(frozen=True)
class BrowserProbe:
    response: FetchResponse
    parsed: ParsedResultFilings | None
    reason: str
    elapsed_seconds: float
    request_count: int
    consent_evidence: str


def assess_download(response: FetchResponse, *, result_period: str | None = None) -> tuple[FetchResponse, ParsedResultFilings | None, str]:
    """Keep raw served bytes and actual URL; never invent scope or timestamps."""
    validate_exchange_dataset_url(response.requested_url, "BSE")
    validate_exchange_dataset_url(response.final_url, "BSE")
    if response.status_code in {401, 403, 429}:
        return replace(response, fetch_status="rejected"), None, "ACCESS_BLOCKED"
    if response.status_code >= 400:
        return replace(response, fetch_status="error"), None, "HTTP_ERROR"
    try:
        parsed = parse_result_filings(response.content, BSE_FINANCIAL_RESULTS, result_period=result_period)
    except ValueError as exc:
        return replace(response, fetch_status="rejected"), None, f"INVALID_DATASET: {exc}"
    headers = {h.strip().upper() for h in parsed.headers}
    # The legacy current-export parser defaults missing scope to standalone.
    # That is insufficient for an API replacement; reject that inference.
    scope_bearing = bool(headers & {"CONSOLIDATED", "RESULT TYPE", "TYPE", "REPORTING SCOPE"})
    integrated = {"QUARTER_CODE", "XMLNAME", "CONSOL_XMLNAME"} <= headers
    if not (scope_bearing or integrated):
        return replace(response, fetch_status="rejected"), None, "MISSING_REPORTING_SCOPE"
    if not parsed.filings or parsed.rejected:
        return replace(response, fetch_status="rejected"), None, "EMPTY_OR_REJECTED_ROWS"
    return replace(response, fetch_status="ok"), parsed, "PARSED_PARTIAL_SURFACE"


def probe_once(*, consent_evidence: str, result_period: str, timeout_ms: int = 30000) -> BrowserProbe:
    """FetchResponse/ParsedResultFilings candidate, with no persistence.

    consent_evidence is an operator permission record, not a grant produced by
    this function. Parser success does not establish historical completeness or
    qualify this recent-publications surface to advance a production watermark.
    """
    if not consent_evidence.strip():
        raise ValueError("BSE express written consent evidence is required before browser launch")
    if not 1000 <= timeout_ms <= 30000:
        raise ValueError("timeout_ms must be between 1000 and 30000")
    refuse_blocked_terms_target(PAGE_URL)
    validate_exchange_dataset_url(PAGE_URL, "BSE")
    # Validate before launch, even if the eventual response has no rows.
    _validate_period(result_period)
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    request_count = 0
    denied = []
    from urllib.parse import urlsplit

    def remaining():
        return max(1, int((deadline - time.monotonic()) * 1000))

    def observed(response):
        if response.url.startswith("https://api.bseindia.com/") and response.status in {401, 403, 429}:
            denied.append(response)

    def fetched(response):
        headers = response.headers
        return FetchResponse(
            requested_url=response.request.url, final_url=response.url,
            status_code=response.status, content=response.body(),
            content_type=headers.get("content-type"), encoding=None,
            etag=headers.get("etag"), last_modified=headers.get("last-modified"),
            auth_mode="public_browser", fetch_status="rejected",
        )

    def outcome(response, parsed, reason):
        return BrowserProbe(response, parsed, reason, round(time.monotonic() - started, 3),
                            request_count, consent_evidence)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False, timeout=remaining())
        try:
            context = browser.new_context()
            page = context.new_page()

            def route(request_route):
                nonlocal request_count
                if denied or time.monotonic() >= deadline:
                    request_route.abort()
                    return
                request = request_route.request
                if request.is_navigation_request():
                    try:
                        validate_exchange_dataset_url(request.url, "BSE")
                        refuse_blocked_terms_target(request.url)
                    except ValueError:
                        request_route.abort()
                        return
                request_count += 1
                request_route.continue_()

            page.route("**/*", route)
            page.on("response", observed)
            # Observe the application's own response, including its actual
            # default query (FlagDur=1), without replaying the blocked API URL.
            event = page.expect_response(
                lambda r: urlsplit(r.url).hostname == "api.bseindia.com"
                and urlsplit(r.url).path == RESULT_PATH, timeout=remaining())
            document = None
            try:
                with event as pending:
                    document = page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=remaining())
                    if document is not None and document.status >= 400:
                        response = fetched(document)
                        reason = "ACCESS_BLOCKED" if document.status in {401, 403, 429} else "HTTP_ERROR"
                        # Cancel the expectation through the exception path.
                        raise _DocumentRejected(response, reason)
                response, parsed, reason = assess_download(fetched(pending.value), result_period=result_period)
                return outcome(response, parsed, reason)
            except _DocumentRejected as exc:
                return outcome(exc.response, None, exc.reason)
            except BrowserTimeout:
                if denied:
                    return outcome(fetched(denied[0]), None, "ACCESS_BLOCKED")
                if document is None:
                    raise RuntimeError("BSE browser navigation timed out without a document")
                return outcome(replace(fetched(document), fetch_status="rejected"), None,
                               "BROWSER_TIMEOUT_OR_MISSING_RESULTS")
        finally:
            browser.close()


class _DocumentRejected(Exception):
    def __init__(self, response, reason):
        self.response, self.reason = response, reason


def daily_fallback(*, receipt_dir: Path, consent_evidence: str, result_period: str,
                   now: datetime | None = None, probe=probe_once) -> BrowserProbe:
    """One morning and one evening attempt per IST day; failures consume a slot.

    Receipts are isolated local evidence, never exchange DB/state. Repeated
    calls fail before launching rather than silently using yesterday's data.
    An interrupted attempt keeps its receipt and is not automatically retried.
    """
    if not consent_evidence.strip():
        raise ValueError("BSE express written consent evidence is required")
    _validate_period(result_period)
    local = (now or datetime.now(ZoneInfo("Asia/Kolkata"))).astimezone(ZoneInfo("Asia/Kolkata"))
    day = local.date().isoformat()
    slot = "morning" if local.hour < 12 else "evening"
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    # Preserve attempts recorded under the original single-attempt policy.
    # Updating the guard must not unlock another attempt on that legacy day.
    if (receipt_dir / f"{day}.json").exists():
        raise FileExistsError(f"Legacy daily receipt already exists for {day}")
    receipt = receipt_dir / f"{day}-{slot}.json"
    # Exclusive creation is also the cross-process slot budget/lock.
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump({"day_ist": day, "slot_ist": slot, "status": "attempt_started", "consent_evidence": consent_evidence}, handle)
    try:
        result = probe(consent_evidence=consent_evidence, result_period=result_period)
        raw_path = receipt_dir / f"{day}-{slot}.raw"
        raw_path.write_bytes(result.response.content)
        metadata = {k: v for k, v in vars(result.response).items() if k != "content"}
        receipt.write_text(json.dumps({"day_ist": day, "slot_ist": slot, "status": result.reason,
            "consent_evidence": consent_evidence, "response": metadata,
            "raw_file": str(raw_path), "elapsed_seconds": result.elapsed_seconds,
            "request_count": result.request_count, "result_period": result_period,
            "accepted_row_count": result.parsed.accepted_row_count if result.parsed else 0,
            "coverage": "public_page_default_window; not historical completeness"}, indent=2) + "\n")
        return result
    except Exception as exc:
        receipt.write_text(json.dumps({"day_ist": day, "slot_ist": slot, "status": "failed",
            "error_type": type(exc).__name__, "consent_evidence": consent_evidence}, indent=2) + "\n")
        raise


def fetch_daily_browser_fallback(url: str, exchange: str, *, receipt_dir: Path,
                                consent_evidence: str, result_period: str) -> FetchResponse:
    """Injectable existing acquisition fetcher for a selected BSE fallback.

    The caller selects this after an API failure/hold; this does not request the
    denied HTTP endpoint or clear its hold. Normal scheduler holds still apply.
    Only the registry's financial-results dataset can use this alternative.
    """
    from ..sources.exchange.datasets import default_url_for
    if exchange != "BSE" or url != default_url_for(BSE_FINANCIAL_RESULTS):
        raise ValueError("Browser fallback supports only the configured BSE financial-results dataset")
    return daily_fallback(receipt_dir=receipt_dir, consent_evidence=consent_evidence,
                          result_period=result_period).response


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consent-evidence", required=True)
    parser.add_argument("--result-period", required=True)
    parser.add_argument("--receipt-dir", type=Path, default=Path("artifacts/bse-browser-results"))
    args = parser.parse_args()
    result = daily_fallback(receipt_dir=args.receipt_dir,
                            consent_evidence=args.consent_evidence,
                            result_period=args.result_period)
    print(json.dumps({"status": result.reason, "http_status": result.response.status_code,
        "source_url": result.response.final_url,
        "accepted_row_count": result.parsed.accepted_row_count if result.parsed else 0,
        "elapsed_seconds": result.elapsed_seconds, "production_writes": 0}))
    return 0 if result.parsed is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
