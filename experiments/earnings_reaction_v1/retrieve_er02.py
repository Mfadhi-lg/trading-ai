"""One-shot, owner-approved ER-02 tranche-1 exchange discovery; no retries.

Reuses the research module's receipt conventions and system curl transport.
No credentials, session bootstrap, redirects, pagination or attachment requests.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from . import er02, event_isolation as er

APPROVED_SHA256='7a1636fea87910a3bcf8d59f59912b78da6786b01ee9dd8442161cb024fd1572'
RUN=er02.OUT/'tranche-1'


def now():return datetime.now(timezone.utc).isoformat()


def plan(manifest, digest):
    if digest != APPROVED_SHA256:raise ValueError('approved manifest hash mismatch')
    requests=[r for r in manifest['requests'] if r['tranche']==1]
    if len(requests)!=8 or len({r['url'] for r in requests})!=8:raise ValueError('exact eight requests required')
    for r in requests:
        host=urlparse(r['url'])
        if (host.scheme!='https' or host.hostname not in {'www.nseindia.com','api.bseindia.com'}
                or r['method']!='GET' or r['max_attempts']!=1 or r['max_response_bytes']!=4_000_000
                or r['timeout_seconds']!=30 or r['event_id'] not in {'FY2026-Q4:132','FY2027-Q1:142'}):
            raise ValueError('approved request policy mismatch')
    return requests


def fetch(request, directory, number):
    """One system-curl invocation; preserve safe response metadata and exact body."""
    begun=now()
    with tempfile.TemporaryDirectory(prefix='transport-',dir=directory) as tmp:
        body=Path(tmp)/'body';headers=Path(tmp)/'headers'
        result=subprocess.run(['/usr/bin/curl','--disable','--silent','--show-error',
            '--proto','=https','--proto-redir','=https','--max-redirs','0','--retry','0',
            '--connect-timeout','10','--max-time','30','--max-filesize','4000000',
            '--request','GET','--header','Accept: application/json',
            '--user-agent','TradingAI-ER02/1.0',
            '--dump-header',str(headers),'--output',str(body),
            '--write-out','%{http_code}\n%{url_effective}\n%{content_type}',request['url']],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=35)
        parts=result.stdout.decode(errors='replace').split('\n')
        status=int(parts[0]) if parts and parts[0].isdigit() else 0
        final=parts[1] if len(parts)>1 else request['url']
        content_type=parts[2] if len(parts)>2 else ''
        raw=body.read_bytes() if body.exists() else b''
        if len(raw)>request['max_response_bytes']:raise ValueError('response body exceeds approved byte cap')
        digest=hashlib.sha256(raw).hexdigest()
        path=directory/f'{number:02d}-{digest}.body'
        path.write_bytes(raw)
        safe_headers={}
        if headers.exists():
            for line in headers.read_text(errors='replace').splitlines():
                key,sep,value=line.partition(':')
                if sep and key.lower() in {'date','content-type','content-length','etag','last-modified','server','retry-after'}:
                    safe_headers[key.lower()]=value.strip()
        receipt=dict(request_id=request['request_id'],event_id=request['event_id'],boundary=request['boundary'],
            exchange=request['exchange'],requested_url=request['url'],final_url=final,http_status=status,
            attempted_at=begun,captured_at=now(),response_sha256=digest,response_bytes=len(raw),
            content_type=content_type,safe_response_headers=safe_headers,curl_exit_code=result.returncode,
            body_path=str(path.relative_to(er02.OUT)),
            provenance_locator=f'ER-02 owner-approved tranche-1 request {number}; approved manifest {APPROVED_SHA256}',
            transport='system curl, verified HTTPS, no credentials/cookies/bootstrap, zero retries, no redirects',
            error_kind='CURL_TRANSPORT_FAILURE' if result.returncode else None)
        receipt_path=directory/f'{number:02d}.receipt.json';er.dump(receipt_path,receipt)
        receipt['receipt_path']=str(receipt_path.relative_to(er02.OUT))
        return receipt,raw


def stop_reason(receipt, raw):
    if receipt['http_status'] in (401,403,429):return 'EXCHANGE_ACCESS_RESTRICTION'
    if receipt['curl_exit_code']:return 'TRANSPORT_FAILURE_NO_RETRY'
    if receipt['http_status']!=200:return 'HTTP_FAILURE_OR_REDIRECT_NO_RETRY'
    lower=raw[:65536].lower()
    if any(marker in lower for marker in (b'access denied',b'captcha',b'request rejected',b'please enable javascript')):
        return 'EXCHANGE_ACCESS_RESTRICTION'
    try:json.loads(raw)
    except (ValueError,UnicodeError):return 'NON_JSON_RESPONSE_NO_BYPASS'
    return None


def execute(requests, directory=RUN, transport=fetch):
    directory.mkdir(parents=True,exist_ok=True)
    ledger=directory/'EXECUTION.json'
    state=dict(approved_manifest_sha256=APPROVED_SHA256,approval_reference="Owner replied 'yes i approve' to tranche-1 eight-GET manifest approval request",started_at=now(),
        approved_requests=requests,responses=[],stop_reason=None,requests_not_attempted=[])
    # Exclusive ledger creation prevents a second tranche invocation/retry.
    with ledger.open('x') as handle:json.dump(state,handle,indent=2)
    for number,request in enumerate(requests,1):
        # Save intent before any outbound operation; an interrupted call consumes its attempt.
        state['inflight_request_id']=request['request_id'];er.dump(ledger,state)
        try:
            receipt,raw=transport(request,directory,number)
        except Exception as exc:
            state['stop_reason']='TRANSPORT_EXCEPTION_NO_RETRY'
            state['exception_type']=type(exc).__name__
            break
        state['responses'].append(receipt)
        state['inflight_request_id']=None
        state['stop_reason']=stop_reason(receipt,raw)
        er.dump(ledger,state)
        if state['stop_reason']:break
    started_ids={r['request_id'] for r in state['responses']}
    if state.get('inflight_request_id'):started_ids.add(state['inflight_request_id'])
    state['requests_not_attempted']=[r['request_id'] for r in requests if r['request_id'] not in started_ids]
    state['finished_at']=now();state['complete_all_eight']=len(state['responses'])==8 and not state['stop_reason']
    er.dump(ledger,state)
    return state


def audit_retained():
    """Offline review of the actual approved responses, preserving prior reviews."""
    from datetime import timedelta
    from .verification import verify_event_boundaries
    state=json.loads((RUN/'EXECUTION.json').read_text())
    observations=[];candidate=None
    for receipt in state['responses']:
        raw=(er02.OUT/receipt['body_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['response_sha256']:raise ValueError('retained body changed')
        if receipt['http_status']!=200 or receipt['curl_exit_code']!=0:continue
        rows=json.loads(raw)
        if not isinstance(rows,list):continue
        for index,row in enumerate(rows):
            observations.append(dict(request_id=receipt['request_id'],row_pointer='/'+str(index),
                symbol=row.get('symbol'),native_announcement_id=row.get('seq_id'),
                broadcast_raw=row.get('an_dt'),dissemination_raw=row.get('exchdisstime'),
                source_category=row.get('desc'),source_subject=row.get('attchmntText'),attachment_url=row.get('attchmntFile'),
                actual_attachment_verified=False,earliest_release_verified=False))
            if (receipt['request_id']=='FY2026-Q4:132:start:NSE' and row.get('seq_id')=='106594219'
                    and row.get('symbol')=='E2E' and row.get('desc')=='Outcome of Board Meeting'
                    and 'Audited financial results for the period ended March 31, 2026' in row.get('attchmntText','')):
                candidate=dict(exchange='NSE',exchange_key='E2E',native_announcement_id=row['seq_id'],
                    announcement=dict(body_path=receipt['body_path'],receipt_path=receipt['receipt_path'],sha256=receipt['response_sha256']),
                    row_pointer='/'+str(index),identity_field='symbol',native_id_field='seq_id',
                    public_timestamp_field='exchdisstime',attachment_url_field='attchmntFile',
                    source_results_claim=row['attchmntText'],attachment_url_pending_approval=row['attchmntFile'])
    er.write_csv('ANNOUNCEMENT_OBSERVATIONS.csv',observations,out=RUN)
    verified=None
    if candidate:
        review_path=er02.OUT/'DISCLOSURE_REVIEWS.json';reviews=json.loads(review_path.read_text())
        before=RUN/'pre-retrieval/DISCLOSURE_REVIEWS.json'
        if not before.exists():shutil.copyfile(review_path,before)
        existing=reviews.get('FY2026-Q4:132',{}).get('start')
        if existing and existing!=candidate:raise ValueError('existing different adjudication requires review')
        reviews.setdefault('FY2026-Q4:132',{})['start']=candidate
        er.dump(review_path,reviews)
        verified=verify_event_boundaries(reviews['FY2026-Q4:132'],company_id=132,period_end='2026-03-31',
            next_period_end='2026-06-30',cutoff=json.loads((er02.OUT/'PILOT_INPUTS.json').read_text())['cutoff'],evidence_root=er02.OUT)
        er.dump(RUN/'E2E_TIMESTAMP_VERIFICATION.json',verified)
    # New proposal only: no calls and no reuse of the stopped execution ledger.
    remaining=[r for r in state['approved_requests'] if r['request_id'] in state['requests_not_attempted'] and r['exchange']=='NSE']
    continuation=[dict(r) for r in remaining]
    if candidate:
        continuation.insert(0,dict(request_id='FY2026-Q4:132:start:NSE:106594219:attachment',event_id='FY2026-Q4:132',boundary='start',exchange='NSE',
            method='GET',url=candidate['attachment_url_pending_approval'],purpose='Inspect actual financial results attached to independently timestamp-verified board outcome',
            expected_type='application/pdf',max_attempts=1,max_response_bytes=4_000_000,timeout_seconds=30))
    proposal=dict(version='ER-02-NSE-continuation-v1',approval_status='AWAITING_NEW_OWNER_APPROVAL_AFTER_GLOBAL_STOP',
        previous_manifest_sha256=APPROVED_SHA256,reason='BSE returned HTTP403; original tranche stopped. New scope contains no BSE retry or bypass.',
        request_count=len(continuation),requests=continuation,
        limits=dict(serial=True,retries=0,credentials=False,redirects=False,stop_on=[401,403,429],pagination=False,other_attachments=False),
        limitations='Earliest-release completeness and cross-venue applicability remain independent evidence requirements; successful retrieval does not automatically establish a window.')
    er.dump(er02.OUT/'NSE_CONTINUATION_MANIFEST.json',proposal)
    (er02.OUT/'NSE_CONTINUATION_MANIFEST.sha256').write_text(er.sha(er02.OUT/'NSE_CONTINUATION_MANIFEST.json')+'\n')
    er.dump(RUN/'AUDIT_SUMMARY.json',dict(http_responses=len(state['responses']),official_nse_records=len(observations),
        bse_http_status=next((r['http_status'] for r in state['responses'] if r['exchange']=='BSE'),None),
        unattempted_requests=len(state['requests_not_attempted']),stop_reason=state['stop_reason'],
        verified_announcement_timestamp_ist=verified['start']['publication_at_ist'] if verified else None,
        earliest_start_verified=verified['start_verified'] if verified else False,complete_windows=0,
        proposed_next_requests=len(continuation),network_calls_in_audit=0))
    print(er.J(json.loads((RUN/'AUDIT_SUMMARY.json').read_text())))


def main():
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--execute-approved-tranche-1',action='store_true');group.add_argument('--audit-retained-tranche-1',action='store_true');args=parser.parse_args()
    if args.audit_retained_tranche_1:return audit_retained()
    if not args.execute_approved_tranche_1:raise ValueError('explicit execution flag required')
    manifest_path=er02.OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json'
    requests=plan(json.loads(manifest_path.read_text()),er.sha(manifest_path))
    RUN.mkdir(parents=True,exist_ok=True)
    if (RUN/'EXECUTION.json').exists():raise ValueError('tranche already attempted; no automatic retry authorized')
    # DNS-only environment preflight is not an exchange GET. This permits the
    # required sandbox escalation before consuming the one-shot HTTP ledger.
    try:socket.getaddrinfo(urlparse(requests[0]['url']).hostname,443,type=socket.SOCK_STREAM)
    except OSError as exc:
        er.dump(RUN/'ENVIRONMENT_CHECK.json',dict(at=now(),kind='DNS_UNAVAILABLE_BEFORE_HTTP',error_type=type(exc).__name__,http_requests_sent=0))
        raise SystemExit('DNS_UNAVAILABLE_BEFORE_HTTP')
    # Preserve the approved review artifacts; never rewrite the signed-off manifest.
    archive=RUN/'pre-retrieval';archive.mkdir(exist_ok=True)
    for name in ['VALIDATION_REPORT.md','ER-02-review.zip','PILOT_VERIFICATION.csv','SUMMARY.json']:
        target=archive/name
        if not target.exists():shutil.copyfile(er02.OUT/name,target)
    er.dump(RUN/'APPROVAL.json',dict(approval_reference="User: yes i approve",approved_scope='tranche 1 only; eight exact official exchange GETs',manifest_sha256=APPROVED_SHA256,recorded_at=now(),request_ids=[r['request_id'] for r in requests]))
    state=execute(requests)
    print(er.J(dict(responses=len(state['responses']),stop_reason=state['stop_reason'],not_attempted=len(state['requests_not_attempted']))))

if __name__=='__main__':main()
