"""Bounded additional-indicator preparation and two public Treasury GETs.

No authenticated supplemental acquisition is implemented or authorized here.
"""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from .prepare_narrowed import ROOT, OUT, BASE, sha
from .execute_narrowed import save

EXTRA = OUT / 'additional'


def prepare():
    if (EXTRA / 'PUBLIC_TREASURY_LEDGER.json').exists():
        raise SystemExit('Public scope already attempted; do not overwrite its manifests or ledger.')
    EXTRA.mkdir(exist_ok=True)
    catalog = BASE / 'inputs/global.json.gz'
    records = json.loads(gzip.decompress(catalog.read_bytes()))
    original = json.loads((BASE / 'UPSTOX_DIAGNOSTIC_MANIFEST.json').read_text())
    gift = [r for r in original['requests'] if r['number'] in (4, 15, 16, 17)]
    assert len(gift) == 4 and all(r['series'] == 'GIFT_NIFTY' for r in gift)
    dow_ledger = ROOT / 'artifacts/technical-integration-v1/retrieval-execution.json'
    dow = next(r for r in json.loads(dow_ledger.read_text())['requests'] if r['series'] == 'DJIA')
    assert sha(ROOT / dow['retained_path']) == dow['response_sha256']
    search = [r for r in records if any(s in json.dumps(r).lower() for s in ('treasury', 'yield', 'us02', 'us10', 'us30y'))]
    save(EXTRA / 'RETAINED_AND_CATALOG_REVIEW.json', dict(
        catalog_file=str(catalog.relative_to(ROOT)), catalog_sha256=sha(catalog), catalog_records=len(records),
        catalog_snapshot_utc='2026-11-04T12:20:06.106105+00:00',
        treasury_yield_matches=search,
        gift_identity=next(r for r in records if r['instrument_key'] == 'GLOBAL_INDEX|SGX NIFTY'),
        gift_definition='Generic GIFT NIFTY quotation; blank instrument_type/underlying/asset/quote-unit fields and no expiry. Not a specific futures contract.',
        dow_retained=dow, dow_reacquisition_required=False,
        absent_exact_identities=['Nasdaq Composite', 'CME ES outrights', 'CME NQ outrights', 'US Treasury nominal 2Y/10Y/30Y yields'],
        finding_scope='No verified equivalent in the retained official global catalog and reviewed Upstox instruments documentation; no guessed instrument or unrelated substitute.'))
    pending = dict(task='TECH-06-ADDITIONAL-GLOBAL', status='PENDING_OWNER_AUTHORIZATION;NOT_EXECUTED',
        reason='Only missing generic GIFT quotation history; retain Dow and completed 16-request results. Treasury uses separate public manifest.',
        metadata={str(catalog.relative_to(ROOT)): sha(catalog)},
        request_count=4, approved_request_count=0, automatic_retries=0, max_authorized_spend_usd=0,
        execution_order=[4, 15, 16, 17],
        policy='Daily first then three minute windows, serial >=1s, no retries; same per-key/interval and global stop rules as approved reduced manifest. Generic quotation only, no futures identity claim.',
        requests=gift,
        not_included=['Dow reacquisition', 'Treasury authenticated endpoints', 'ES/NQ purchase', 'Nasdaq Composite licensed purchase', 'any NSE index requests'])
    save(EXTRA / 'PENDING_GIFT_MANIFEST.json', pending)
    (EXTRA / 'PENDING_GIFT_MANIFEST.sha256').write_text(sha(EXTRA / 'PENDING_GIFT_MANIFEST.json') + '  PENDING_GIFT_MANIFEST.json\n')
    public = dict(task='TECH-06-PUBLIC-TREASURY', status='PUBLIC_NO_AUTH_NO_PAYMENT;BOUNDED_TWO_GETS',
        authorization_basis='Owner additional-indicator instruction permits official public-source investigation; prior authorization required only for new authenticated or paid retrievals.',
        request_count=2, automatic_retries=0, cost_usd=0, authenticated=False,
        semantic_definition='Treasury daily nominal par constant-maturity yields, percent per annum, 2/10/30 years; not bill discount yields, real yields, bond prices or ETF prices.',
        retained_observation_range=['2025-11-20', '2026-10-30'],
        baseline_reason='November observations permit previous-US-observation reference and changes at the 2025-12-01 Indian checkpoint.',
        response_bounds='Each documented annual XML response retained intact as evidence; analysis filters dates and tenors. No additional pages, retries or fallback calls.',
        source_documentation='https://home.treasury.gov/treasury-daily-interest-rate-xml-feed',
        requests=[dict(number=i, year=year, method='GET', url='https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=' + str(year)) for i, year in enumerate((2025, 2026), 1)])
    save(EXTRA / 'PUBLIC_TREASURY_MANIFEST.json', public)
    print(json.dumps(dict(public_treasury_requests=2, pending_gift_requests=4,
        pending_gift_manifest_sha256=sha(EXTRA / 'PENDING_GIFT_MANIFEST.json'), retained_dow_rows=dow['rows'], treasury_catalog_matches=len(search)), indent=2))


def retrieve_public():
    plan_path = EXTRA / 'PUBLIC_TREASURY_MANIFEST.json'
    plan = json.loads(plan_path.read_text())
    assert len(plan['requests']) == 2 and plan['authenticated'] is False and plan['cost_usd'] == 0
    ledger = EXTRA / 'PUBLIC_TREASURY_LEDGER.json'
    state = dict(manifest_sha256=sha(plan_path), started_at=datetime.now(timezone.utc).isoformat(),
                 requests=[], complete=False, automatic_retries=0, credentials_read=False)
    with ledger.open('x') as stream:
        json.dump(state, stream, indent=2)
    raw_dir = EXTRA / 'raw'
    raw_dir.mkdir(exist_ok=True)
    for r in plan['requests']:
        if state['requests']:
            time.sleep(1.05)
        rec = dict(r, attempted_at=datetime.now(timezone.utc).isoformat())
        state['requests'].append(rec)
        save(ledger, state)
        result = subprocess.run(['/usr/bin/curl', '--disable', '--silent', '--proto', '=https',
            '--max-redirs', '0', '--retry', '0', '--max-time', '40', '--request', 'GET',
            '--write-out', '\n%{http_code}', r['url']], capture_output=True)
        rec['received_at'] = datetime.now(timezone.utc).isoformat()
        if result.returncode:
            rec.update(status='TRANSPORT_STOP_NO_RETRY', curl_exit_code=result.returncode)
            save(ledger, state)
            break
        raw, code = result.stdout.rsplit(b'\n', 1)
        rec.update(http_status=int(code), response_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        if int(code) != 200:
            rec['status'] = 'HTTP_STOP_NO_RETRY'
            save(ledger, state)
            break
        path = raw_dir / (str(r['year']) + '-' + rec['response_sha256'] + '.xml')
        path.write_bytes(raw)
        rec['retained_path'] = str(path.relative_to(ROOT))
        try:
            tree = ET.fromstring(raw)
            properties = tree.findall('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties')
            if not properties:
                raise ValueError('no Treasury properties')
            rec.update(status='SUCCESS', returned_dates=len(properties))
        except ET.ParseError:
            rec['status'] = 'SCHEMA_STOP_NO_RETRY'
        save(ledger, state)
        print(json.dumps(rec), flush=True)
        if rec['status'] != 'SUCCESS':
            break
    state['complete'] = len(state['requests']) == 2 and all(r['status'] == 'SUCCESS' for r in state['requests'])
    state['finished_at'] = datetime.now(timezone.utc).isoformat()
    save(ledger, state)


if __name__ == '__main__':
    if sys.argv[1:] == ['retrieve-public']:
        retrieve_public()
    elif not sys.argv[1:]:
        prepare()
    else:
        raise SystemExit('Use no arguments to prepare or retrieve-public for the two public GETs.')
