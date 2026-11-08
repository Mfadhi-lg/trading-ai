"""Execute only the owner's bounded manifest; reuse immutable receipts on restart.

No recurring authorization is inferred. Run with --authorize SHA256.
"""
import argparse
import fcntl
import gzip
import json
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from experiments.historical_candle_archive_v1.storage import ROOT, Archive, dump, sha
from experiments.historical_candle_archive_v1.acquire import normalize
from experiments.historical_candle_archive_v1.control import checkpoint
from experiments.technical_integration_v2.execute_narrowed import strict_payload, classify_error
from experiments.technical_integration_v2.narrowed_validation import validate

OUT = ROOT/'artifacts/technical-fix-v2'
MANIFEST = OUT/'BOUNDED_ACQUISITION_MANIFEST.json'
APPROVED = '8925d777c7294e16240ba5933c4130fb2aded3e323fa5d80fe39c147801a5ff6'


def now():
    return datetime.now(timezone.utc).isoformat()


def get(q, token):
    headers = 'header = "Accept: application/json"\n'
    if q['kind'] != 'TREASURY':
        if any(c in token for c in '\r\n"\\'):
            raise ValueError('unsafe credential')
        headers += 'header = "Authorization: Bearer '+token+'"\n'
    p = subprocess.run(['/usr/bin/curl','--disable','--config','-','--silent',
        '--proto','=https','--max-redirs','0','--retry','0','--max-time','45',
        '--write-out','\n%{http_code}',q['url']],input=headers.encode(),capture_output=True)
    if p.returncode:
        raise RuntimeError('STOP_TRANSPORT_EXIT_'+str(p.returncode))
    raw, status = p.stdout.rsplit(b'\n',1)
    if token and token.encode() in raw:
        raise RuntimeError('STOP_UNSAFE_CREDENTIAL_ECHO')
    return raw, int(status)


def persist_stock(a, q, rec, raw):
    """Same archive schema/parser, with raw-before-normalization recovery."""
    params = dict(q)
    a.db.execute('''INSERT OR IGNORE INTO requests
        (request_id,company_id,instrument_key,unit,interval,from_date,to_date,status,attempts)
        VALUES (?,?,?,?,?,?,?,'RAW_SAVED',1)''', tuple(q[k] for k in
        ('request_id','company_id','instrument_key','unit','interval','from_date','to_date')))
    a.db.execute("UPDATE requests SET raw_sha256=?,raw_path=?,received_at=?,http_status=200,status='RAW_SAVED' WHERE request_id=?",
        (rec['sha256'],rec['raw_path'],rec['received_at'],q['request_id']))
    a.db.commit()
    rows, issues = normalize(raw,params,rec['received_at'],quarantine_negative=True)
    a.parquet(rows,params,rec['sha256'],rec['received_at'])
    a.db.execute("UPDATE requests SET status='SUCCESS',row_count=? WHERE request_id=?",(len(rows),q['request_id']))
    a.db.commit()
    rec.update(accepted=sum(r['quality']=='OHLC_VALID' for r in rows),quarantined=sum(r['quality']!='OHLC_VALID' for r in rows),issues=issues)


def execute(authorize, *, fetch=get, archive_factory=Archive, supplier=None, pace=1.05,
            manifest_path=MANIFEST, output=OUT, expected_digest=APPROVED, interrupt_after_raw=False):
    digest = sha(manifest_path.read_bytes())
    if authorize != digest or digest != expected_digest:
        raise PermissionError('STOP_EXACT_OWNER_MANIFEST_AUTHORIZATION_REQUIRED')
    plan = json.loads(manifest_path.read_text())
    checkpoint()
    output.mkdir(parents=True,exist_ok=True)
    ledger_path = output/'UPDATE_LEDGER.json'
    with (output/'.update.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(
            manifest_sha256=digest,started_at=now(),requests={},automatic_retries=0)
        if state['manifest_sha256'] != digest:
            raise RuntimeError('STOP_LEDGER_SCOPE_CHANGED')
        a = archive_factory()
        token = None
        try:
            state.update(status='RUNNING',last_error=None)
            dump(ledger_path,state)
            # Public data and global context first; identity and bounds unchanged.
            order = {'TREASURY':0,'GLOBAL':1,'NSE_INDEX':2,'STOCK':3}
            for q in sorted(plan['requests'],key=lambda r:order[r['kind']]):
                checkpoint();a.guard()
                old = state['requests'].get(q['request_id'])
                if old and old['status']=='IN_FLIGHT' and not old.get('raw_path'):
                    raise RuntimeError('STOP_UNCERTAIN_PROVIDER_DELIVERY_NO_AUTOMATIC_RETRY')
                if old and old.get('raw_path'):
                    raw = gzip.decompress((a.root/old['raw_path']).read_bytes())
                    if sha(raw) != old['sha256']:
                        raise RuntimeError('STOP_SAVED_RAW_CHECKSUM')
                    if old['status'] in ('SUCCESS','EMPTY','ISOLATED_INSTRUMENT_REJECTION'):
                        continue
                    rec = old
                else:
                    if q['kind'] != 'TREASURY' and token is None:
                        if supplier is None:
                            from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
                            supplier = secure_supplier
                        token = supplier()
                    state['requests'][q['request_id']]=dict(request_id=q['request_id'],kind=q['kind'],
                        series=q['series'],status='IN_FLIGHT',attempt_started_at=now())
                    dump(ledger_path,state)
                    raw, status = fetch(q,token or '')
                    raw_digest, path = a.raw(raw)
                    rec = dict(request_id=q['request_id'],kind=q['kind'],series=q['series'],
                        url=q['url'],received_at=now(),sha256=raw_digest,raw_path=path,http_status=status,status='RAW_SAVED')
                    state['requests'][q['request_id']] = rec
                    dump(ledger_path,state)  # durable receipt before any normalization
                    if status != 200:
                        action, codes = classify_error(status,strict_payload(raw))
                        if status==400 and codes==['UDAPI1021']:
                            action='SERIES_STOP_PROVIDER_INVALID_INSTRUMENT_KEY'
                        rec.update(status=action,error_codes=codes)
                        if action=='SERIES_STOP_PROVIDER_INVALID_INSTRUMENT_KEY':
                            rec['status']='ISOLATED_INSTRUMENT_REJECTION';dump(ledger_path,state);continue
                        raise RuntimeError('STOP_HTTP_'+str(status)+'_'+action)
                    if interrupt_after_raw:
                        raise InterruptedError('controlled durable raw interruption')
                    time.sleep(pace)
                if rec['http_status'] != 200:
                    action,codes=classify_error(rec['http_status'],strict_payload(raw))
                    if rec['http_status']==400 and codes in (['UDAPI1021'],['UDAPI100011']):
                        rec.update(status='ISOLATED_INSTRUMENT_REJECTION',error_codes=codes)
                        dump(ledger_path,state);continue
                    raise RuntimeError('STOP_UNRESOLVED_HTTP_RECEIPT_'+action)
                if q['kind']=='TREASURY':
                    tree=ET.fromstring(raw)
                    if not tree.findall('{http://www.w3.org/2005/Atom}entry'):
                        raise ValueError('empty Treasury feed')
                    rec.update(accepted=len(tree.findall('{http://www.w3.org/2005/Atom}entry')),quarantined=0)
                elif q['kind']=='STOCK':
                    persist_stock(a,q,rec,raw)
                else:
                    payload=strict_payload(raw)
                    if payload.get('status')!='success' or not isinstance(payload.get('data',{}).get('candles'),list):
                        raise ValueError('STOP_PROVIDER_SCHEMA')
                    accepted,rejected,_=validate(payload['data']['candles'],dict(q,number=q['request_id']),rec['received_at'])
                    rec.update(accepted=len(accepted),quarantined=len(rejected),rejections=rejected)
                rec['status']='SUCCESS' if rec['accepted'] else 'EMPTY'
                state['updated_at']=now();dump(ledger_path,state)
                print(json.dumps(dict(kind=q['kind'],series=q['series'],status=rec['status'],accepted=rec['accepted'],done=len(state['requests']),total=len(plan['requests']))),flush=True)
            state.update(status='COMPLETE',finished_at=now());dump(ledger_path,state)
            return state
        except BaseException as error:
            state.update(status='STOPPED',last_error=str(error),updated_at=now());dump(ledger_path,state)
            raise
        finally:
            a.db.close();a.writer_lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--authorize',required=True)
    execute(p.parse_args().authorize)
