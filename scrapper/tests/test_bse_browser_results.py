"""Offline checks for the browser candidate's acquisition safety boundaries."""
from dataclasses import replace
import json
import pytest

from scrapper.core.http import FetchResponse
from scrapper.research.bse_browser_results import DOWNLOAD_URL, assess_download, probe_once, daily_fallback, BrowserProbe


def response(content, status=200):
    return FetchResponse(DOWNLOAD_URL, DOWNLOAD_URL, status, content, "application/json",
                         None, None, None, "public_browser", "ok")


def integrated(timestamp="2026-10-08T22:05:00"):
    return json.dumps({"Table": [{"Scrip_cd": "532540", "company_name": "Example Ltd",
        "quarter_code": "SQ2026-2027", "Fld_CreateDate": timestamp,
        "XMLName": "standalone.xml", "Consol_XMLName": "consolidated.xml"}]}).encode()


def test_consent_required_before_browser_import_or_launch():
    with pytest.raises(ValueError, match="consent"):
        probe_once(consent_evidence="", result_period="2026-09")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_denial_is_not_empty_publication(status):
    fetch, parsed, reason = assess_download(response(b"Access denied", status))
    assert fetch.status_code == status and fetch.fetch_status == "rejected"
    assert parsed is None and reason == "ACCESS_BLOCKED"


def test_scope_identity_and_raw_provenance_are_preserved():
    raw = integrated()
    fetch, parsed, reason = assess_download(response(raw))
    assert fetch.content == raw and fetch.requested_url == DOWNLOAD_URL
    assert fetch.auth_mode == "public_browser" and fetch.fetch_status == "ok"
    assert {f.reporting_scope for f in parsed.filings} == {"standalone", "consolidated"}
    assert {f.period_end_date for f in parsed.filings} == {"2026-09-30"}
    assert reason == "PARSED_PARTIAL_SURFACE"


def test_new_broadcast_is_distinct_revision_candidate():
    _, first, _ = assess_download(response(integrated()))
    _, revised, _ = assess_download(response(integrated("2026-10-09T10:00:00")))
    assert {f.filing_key for f in first.filings}.isdisjoint({f.filing_key for f in revised.filings})


def test_browser_export_cannot_invent_standalone_scope():
    csv = b"SECURITY CODE,SECURITY NAME,PERIOD,AUDITED/UNAUDITED,BROADCAST DATE & TIME\n532540,Example Ltd,SQ2026-2027,Audited,08-Oct-2026 22:05:00\n"
    fetch, parsed, reason = assess_download(response(csv))
    assert fetch.fetch_status == "rejected" and parsed is None
    assert reason == "MISSING_REPORTING_SCOPE"


def test_html_and_foreign_redirect_never_become_filings():
    fetch, parsed, reason = assess_download(response(b"<html>Access Denied</html>"))
    assert fetch.fetch_status == "rejected" and parsed is None
    with pytest.raises(ValueError):
        assess_download(replace(response(integrated()), final_url="https://example.com/data"))


@pytest.mark.parametrize("fails", [False, True])
def test_daily_budget_survives_new_callers_and_failed_attempts(tmp_path, fails):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    calls = []
    def probe(**kwargs):
        calls.append(kwargs)
        if fails:
            raise RuntimeError("browser unavailable")
        return BrowserProbe(response(integrated()), None, "fixture", 1.0, 1, "hard-copy attestation")
    arguments = dict(receipt_dir=tmp_path, consent_evidence="hard-copy attestation",
                     result_period="2026-09", now=now, probe=probe)
    if fails:
        with pytest.raises(RuntimeError):
            daily_fallback(**arguments)
    else:
        daily_fallback(**arguments)
    with pytest.raises(FileExistsError):
        daily_fallback(**arguments)
    assert len(calls) == 1


def test_morning_evening_budget_uses_ist_noon_boundary(tmp_path):
    from datetime import datetime, timezone
    calls = []
    def probe(**kwargs):
        calls.append(kwargs)
        return BrowserProbe(response(integrated()), None, "fixture", 1, 1, "attestation")
    options = dict(receipt_dir=tmp_path, consent_evidence="attestation",
                   result_period="2026-09", probe=probe)
    morning = datetime(2026, 10, 10, 6, 29, tzinfo=timezone.utc)  # 11:59 IST
    evening = datetime(2026, 10, 10, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    daily_fallback(now=morning, **options)
    with pytest.raises(FileExistsError):
        daily_fallback(now=morning, **options)
    daily_fallback(now=evening, **options)
    with pytest.raises(FileExistsError):
        daily_fallback(now=evening, **options)
    assert len(calls) == 2
    assert (tmp_path / "2026-10-10-morning.json").exists()
    assert (tmp_path / "2026-10-10-evening.json").exists()
    daily_fallback(now=datetime(2026, 10, 11, 0, tzinfo=timezone.utc), **options)
    assert len(calls) == 3


def test_legacy_daily_receipt_is_never_reset_by_new_policy(tmp_path):
    from datetime import datetime, timezone
    (tmp_path / "2026-10-09.json").write_text('{"status":"failed"}')
    def forbidden(**kwargs):
        raise AssertionError("must not relaunch a legacy consumed day")
    with pytest.raises(FileExistsError, match="Legacy"):
        daily_fallback(receipt_dir=tmp_path, consent_evidence="attestation",
            result_period="2026-09", now=datetime(2026, 10, 9, 12, tzinfo=timezone.utc),
            probe=forbidden)


def test_visible_browser_uses_context_event_value_and_closes(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from playwright import sync_api
    from scrapper.research.bse_browser_results import PAGE_URL, RESULT_PATH
    closed = []
    payload = integrated()
    api = SimpleNamespace(url="https://api.bseindia.com" + RESULT_PATH + "?FlagDur=1",
        request=SimpleNamespace(url="https://api.bseindia.com" + RESULT_PATH + "?FlagDur=1"),
        headers={"content-type": "application/json"}, status=200, body=lambda: payload)
    @contextmanager
    def expect(*args, **kwargs):
        assert args[0](api)
        yield SimpleNamespace(value=api)
    page = SimpleNamespace(route=lambda *a: None, on=lambda *a: None,
        expect_response=expect,
        goto=lambda url, **kw: SimpleNamespace(status=200))
    browser = SimpleNamespace(new_context=lambda: SimpleNamespace(new_page=lambda:page),
                              close=lambda:closed.append(True))
    def launch(**kwargs):
        assert kwargs["channel"] == "chrome" and kwargs["headless"] is False
        return browser
    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    monkeypatch.setattr(sync_api, "sync_playwright", playwright)
    result = probe_once(consent_evidence="hard-copy attestation", result_period="2026-09")
    assert result.parsed.accepted_row_count == 2
    assert result.response.content == payload
    assert closed == [True]


def test_acquisition_fetcher_is_result_only_and_preserves_actual_source(monkeypatch, tmp_path):
    from scrapper.research import bse_browser_results as module
    from scrapper.sources.exchange.datasets import default_url_for
    fetched = response(integrated())
    monkeypatch.setattr(module, "daily_fallback", lambda **kw:
        BrowserProbe(fetched, None, "fixture", 1, 1, "attestation"))
    options = dict(receipt_dir=tmp_path, consent_evidence="attestation", result_period="2026-09")
    assert module.fetch_daily_browser_fallback(default_url_for("bse_financial_results"), "BSE", **options) is fetched
    with pytest.raises(ValueError):
        module.fetch_daily_browser_fallback(default_url_for("bse_security_master"), "BSE", **options)
    with pytest.raises(ValueError):
        module.fetch_daily_browser_fallback("https://example.com", "BSE", **options)
