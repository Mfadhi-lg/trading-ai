"""Reassess one retained unattended BSE response; no browser/network replay."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import fcntl,hashlib,json,shutil
from pathlib import Path
from automatic_acquisition import ingest_bse_capture,verify_config
from production_ownership import owner_gate
from scrapper.core.http import FetchResponse
from scrapper.research.bse_browser_results import assess_download
from scrapper.sources.exchange.browser_results import RetainedBSEBrowserResults
import nse_results as nse
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/phase3-completion/bse-retained-reassessment'

def save(name,data):(OUT/name).write_text(json.dumps(data,indent=2)+'\n')
def prepare(original=None):
    if (OUT/'execution-started.json').exists():raise RuntimeError('Retained-input write already attempted; preserve the receipt')
    verify_config();nse.check_configuration();OUT.mkdir(parents=True,exist_ok=True)
    original=original or ROOT/'artifacts/bse-browser-results/2026-10-11-evening.json'
    nse.require(not original.is_symlink(),'Original receipt must not be a symlink')
    original=original.resolve()
    nse.require(original.resolve().parent==(ROOT/'artifacts/bse-browser-results').resolve() and not original.is_symlink(),'Select an original retained BSE receipt')
    rawpath=original.with_suffix('.raw')
    receipt=json.loads(original.read_text());raw=rawpath.read_bytes()
    nse.require(not rawpath.is_symlink() and len(raw)<=10*1024*1024,'Retained BSE source/byte budget invalid')
    stem=receipt['day_ist']+'-'+receipt['slot_ist']
    nse.require(original.stem==stem and receipt['slot_ist'] in ('morning','evening'),'Original receipt date/slot identity differs')
    operation=ROOT/'artifacts/automatic-acquisition'/ (receipt['day_ist']+'-bse-'+receipt['slot_ist'])
    admission=json.loads((operation/'admission.json').read_text());outcome=json.loads((operation/'outcome.json').read_text())
    nse.require(admission['dispatch_origin']=='launchd' and receipt['status']=='EMPTY_OR_REJECTED_ROWS','Expected original unattended parser-fence failure required')
    response,parsed,reason=assess_download(FetchResponse(content=raw,**receipt['response']),result_period=receipt['result_period'])
    nse.require(parsed is not None and not parsed.rejected and 0<parsed.accepted_row_count<=6000,'Exact retained source did not pass corrected validation')
    receipt.update(status=reason,accepted_row_count=parsed.accepted_row_count,response={k:v for k,v in asdict(response).items() if k!='content'})
    receipt['reassessment']={'original_receipt':str(original.relative_to(ROOT)),
        'original_receipt_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
        'original_status':'EMPTY_OR_REJECTED_ROWS','source_requests':0,
        'source_periods':sorted({f.result_period for f in parsed.filings}),
        'reassessed_at_utc':datetime.now(timezone.utc).isoformat(),
        'parser_sha256':hashlib.sha256((ROOT/'scrapper/sources/exchange/result_filings.py').read_bytes()).hexdigest()}
    save('receipt.json',receipt);shutil.copyfile(rawpath,OUT/'response.raw')
    verification={'started_at_utc':admission['started_at_utc'],'finished_at_utc':outcome['finished_at_utc'],
        'raw_sha256':hashlib.sha256(raw).hexdigest(),'raw_bytes':len(raw),'response':receipt['response'],
        'browser_result':reason,'result_period':receipt['result_period'],'source_requests':0,
        'original_receipt_preserved':True,'reinterpretation':'Retained bytes revalidated under the corrected canonical parser; no source period is invented'}
    save('verification.json',verification)
    RetainedBSEBrowserResults.load(receipt_path=OUT/'receipt.json',raw_path=OUT/'response.raw',verification_path=OUT/'verification.json')
    from bse_production import production_config
    base=production_config()
    hashes=dict(base['application_hashes'])
    hashes['scrapper/sources/exchange/result_filings.py']=hashlib.sha256((ROOT/'scrapper/sources/exchange/result_filings.py').read_bytes()).hexdigest()
    save('checks.json',{'application_hashes':hashes,'operator_hashes':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('nse_results_runtime.py','bse_scheduled_runtime.py','production_ownership.py','sqlite_sidecar_guard.py')}})
    return base['operation_image']

def main():
    global OUT
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true')
    p.add_argument('--receipt',type=Path,help='Original failed unattended BSE receipt; never a new browser request')
    p.add_argument('--output-dir',type=Path,help='Separate retained-input recovery evidence directory')
    args=p.parse_args()
    if args.output_dir:
        nse.require(args.output_dir.resolve().is_relative_to((ROOT/'artifacts').resolve()),'Recovery evidence must remain under repository artifacts')
        OUT=args.output_dir.resolve()
    image=prepare(args.receipt)
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        gate=owner_gate()
        nse.require(json.loads(nse.checked(['docker','image','inspect',image]))[0]['Id']==image,'Pinned BSE image changed')
        volume=json.loads(nse.checked(['docker','volume','inspect',nse.VOLUME]))[0]
        nse.require(volume['Labels'].get('com.docker.compose.project')=='trading-ai','Production volume changed')
        if args.execute:
            preflight=json.loads((OUT/'preflight-outcome.json').read_text())
            nse.require(preflight['status']=='PASS','Retained BSE preflight required')
            save('execution-started.json',{'origin':'manual_retained_input_correction','source_requests':0,
                'maximum_source_rows':json.loads((OUT/'receipt.json').read_text())['accepted_row_count'],
                'maximum_new_queue_entries':min(100,json.loads((OUT/'receipt.json').read_text())['accepted_row_count']),
                'owner_gate':gate,'started_at_utc':datetime.now(timezone.utc).isoformat()})
        code=ingest_bse_capture(OUT,image,preflight=not args.execute)
        # Save separate preflight/write logs; retain the original unattended outcome.
        (OUT/'runtime.log').rename(OUT/('ingestion.log' if args.execute else 'preflight.log'))
        save('outcome.json' if args.execute else 'preflight-outcome.json',{'status':'PASS' if code==0 else 'STOP','exit_code':code,
            'source_requests':0,'production_ingestion':args.execute,'original_slot_replayed':False})
        print(json.dumps({'status':'PASS' if code==0 else 'STOP','path':str(OUT),'source_requests':0}));return code
if __name__=='__main__':raise SystemExit(main())
