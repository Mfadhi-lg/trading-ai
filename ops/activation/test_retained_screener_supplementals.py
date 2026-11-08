"""Real retained supplemental fragments; no requests, queue claims or ingestion."""
import ast
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest
from scrapper.sources.screener.metrics import parse_quick_ratios_fragment
from scrapper.sources.screener.commentary import parse_commentary_fragment

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/'artifacts/screener-supplemental-access/20261009T132929396890Z'


@pytest.mark.parametrize('company',['GMBREW','TCS'])
def test_real_quick_ratio_data_has_seventeen_numeric_metrics(company):
    raw=(EVIDENCE/(company+'-quick_ratios.raw')).read_bytes()
    fields,metrics=parse_quick_ratios_fragment(raw)
    assert len(fields)==len(metrics)==17
    assert all(m.value_decimal is not None for m in metrics)
    assert {'EPS','Debt to equity','Profit after tax','Promoter holding'} <= {m.label_raw for m in metrics}
    assert all(f.display_text.strip() for f in fields)


@pytest.mark.parametrize('company',['GMBREW','TCS'])
def test_commentary_is_publisher_premium_notice_not_financial_commentary(company):
    raw=(EVIDENCE/(company+'-commentary.raw')).read_bytes()
    soup=BeautifulSoup(raw,'html.parser')
    text=soup.get_text(' ',strip=True).lower()
    assert 'upgrade to premium' in text and '20 free key-insights' in text
    assert not soup.select('div.sub')
    assert parse_commentary_fragment(raw)==[]


def test_retained_body_hashes_match_observed_responses():
    records=[]
    for line in (EVIDENCE/'runtime.log').read_text().splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if row.get('stage')=='response':records.append(row['result'])
    assert len(records)==4
    for row in records:
        raw=(EVIDENCE/row['raw_file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['content_sha256']
        assert len(raw)==row['bytes'] and row['http_status']==200 and row['fetch_status']=='ok'


def test_existing_financial_scrape_keeps_commentary_optional():
    from scrapper.workflows.screener.ingest import scrape_screener_company_path
    import inspect
    source=inspect.getsource(scrape_screener_company_path)
    assert 'commentary = None' in source and 'commentary = None' in source.split('except')[1]
    assert 'ownership_check' in inspect.getsource(__import__('scrapper.workflows.screener.ingest',fromlist=['ingest_screener_response']).ingest_screener_response)
