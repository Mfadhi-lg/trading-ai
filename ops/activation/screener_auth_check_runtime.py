"""Four bounded supplemental-access checks; no queue claims or financial ingest."""
from contextlib import ExitStack, closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import sys

sys.path[:0]=['/activation','/app']
from nse_results_runtime import run_owned, require


def main():
    from scrapper.operations.coordinator.exchange import ownership
    from scrapper.operations.workers.screener_requests import SharedScreenerClient
    from scrapper.db.database import connect
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    from scrapper.sources.screener.commentary import parse_commentary_fragment
    db=Path('/data/scraper.db')
    require(db.is_file() and not db.is_symlink(),'Existing production DB required')
    endpoints=(
        ('GMBREW','quick_ratios','https://www.screener.in/api/company/301/quick_ratios/'),
        ('GMBREW','commentary','https://www.screener.in/wiki/company/1065/commentary/v2/'),
        ('TCS','quick_ratios','https://www.screener.in/api/company/1161/quick_ratios/'),
        ('TCS','commentary','https://www.screener.in/wiki/company/3365/commentary/v2/'))
    with ExitStack() as stack:
        for role in ('scheduler','executor'):
            require(stack.enter_context(ownership(db,role)),'Existing exchange '+role+' owner')
        run_owned(priority_result_periods(),db,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
        with closing(connect(db)) as conn:
            def state():
                h=hashlib.sha256()
                for table,order in (('screener_company_queue','queue_item_id'),('accepted_page_versions','company_id,source,reporting_scope,resolved_source_period'),('exchange_source_access_events','event_id')):
                    for row in conn.execute('SELECT * FROM '+table+' ORDER BY '+order):
                        h.update(json.dumps(dict(row),sort_keys=True).encode());h.update(b'\n')
                return h.hexdigest()
            before=state()
            client=SharedScreenerClient(conn,owner='bounded-supplemental-access-check')
            if '--retain-content' in sys.argv:
                from scrapper.config import SCREENER_COOKIE_FILE
                from scrapper.core.http import load_screener_cookies
                proof=json.loads(Path('/probe/session-check.json').read_text())
                require(hmac.new(bytes.fromhex(proof['nonce']),SCREENER_COOKIE_FILE.read_bytes(),hashlib.sha256).hexdigest()==proof['file_hmac'],
                        'Mounted session differs from the updated host session')
                cookie=load_screener_cookies(SCREENER_COOKIE_FILE).get('sessionid')
                require(cookie and client.session.cookies.get('sessionid')==cookie and client.transport.cookies.get('sessionid')==cookie,
                        'Canonical client does not carry the updated session')
                print(json.dumps({'stage':'session_verified','configured_session_file':str(SCREENER_COOKIE_FILE),
                    'updated_host_file_matches':True,'canonical_client_cookie_matches':True,
                    'shared_transport_cookie_matches':True,'token_values_logged':False}),flush=True)
            results=[]
            try:
                for company,kind,url in endpoints:
                    print(json.dumps({'stage':'request_started','company':company,'kind':kind,'url':url}),flush=True)
                    method=client.fetch_quick_ratios if kind=='quick_ratios' else client.fetch_commentary
                    response=method(url,'https://www.screener.in/company/'+company+'/')
                    item={'company':company,'kind':kind,'requested_url':response.requested_url,
                        'final_url':response.final_url,'http_status':response.status_code,
                        'fetch_status':response.fetch_status,'bytes':len(response.content),
                        'content_sha256':hashlib.sha256(response.content).hexdigest()}
                    if kind=='commentary' and response.fetch_status=='ok':
                        item['commentary_observations']=len(parse_commentary_fragment(response.content))
                    if '--retain-content' in sys.argv:
                        raw=Path('/probe')/(company+'-'+kind+'.raw')
                        raw.write_bytes(response.content);os.chmod(raw,0o600)
                        item['raw_file']=raw.name
                    results.append(item)
                    print(json.dumps({'stage':'response','result':item}),flush=True)
                    if response.status_code in (401,403,429):
                        break
            finally:
                client.close()
            require(state()==before,'Unexpected queue, financial acceptance or exchange hold mutation')
            print(json.dumps({'stage':'verified','results':results,'queue_claims':0,
                'financial_ingestion':False,'business_state_unchanged':True,'automatic_retry':False}),flush=True)
            return 0 if len(results)==4 and all(r['http_status']==200 and r['fetch_status']=='ok' for r in results) else 1


if __name__=='__main__':
    signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('300-second check deadline')))
    signal.alarm(300)
    try:sys.exit(main())
    except Exception as exc:
        from scrapper.workflows.screener.company_worker import _safe_error
        print(json.dumps({'stage':'stop','error':_safe_error(exc),'automatic_retry':False}),flush=True)
        sys.exit(2)
