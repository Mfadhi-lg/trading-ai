"""Captured unattended BSE response: priority hint must not discard source periods."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from scrapper.core.http import FetchResponse
from scrapper.research.bse_browser_results import assess_download
from scrapper.sources.exchange.result_filings import parse_result_filings

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'artifacts/bse-browser-results/2026-10-11-evening.raw'
RECEIPT=ROOT/'artifacts/bse-browser-results/2026-10-11-evening.json'

def test_retained_unattended_response_preserves_march_revision():
    receipt=json.loads(RECEIPT.read_text());raw=RAW.read_bytes()
    response=FetchResponse(content=raw,**receipt['response'])
    checked,parsed,reason=assess_download(response,result_period='2026-09')
    assert checked.fetch_status=='ok' and reason=='PARSED_PARTIAL_SURFACE'
    assert parsed.accepted_row_count==2 and not parsed.rejected
    assert {f.result_period for f in parsed.filings}=={'2026-03'}
    assert {f.reporting_scope for f in parsed.filings}=={'standalone','consolidated'}
    assert all(f.broadcast_at_utc=='2026-10-11T09:56:10+00:00' for f in parsed.filings)
    assert all(f.broadcast_raw=='2026-10-11T15:26:10.95' for f in parsed.filings)
    assert checked.content==raw and checked.final_url==response.final_url
    assert all(f.exchange_key=='504132' for f in parsed.filings)

@pytest.mark.parametrize('code',['SQ2026-2028','bad-code','MC2026-2028'])
def test_malformed_period_does_not_become_a_filing(code):
    data=json.loads(RAW.read_bytes());data['Table']=[data['Table'][0]];data['Table'][0]['quarter_code']=code
    parsed=parse_result_filings(json.dumps(data).encode(),'bse_financial_results',result_period='2026-09')
    assert parsed.rejected and not parsed.filings
