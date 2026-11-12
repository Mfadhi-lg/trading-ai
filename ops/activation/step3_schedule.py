"""Bounded Step 3 jobs for the existing IST admission loop; no standing writer."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def settings():
    config=json.loads((HERE/'step3_schedule.json').read_text())
    if (not 1<=config['financial_max_companies']<=10 or config['cap_max_checks']!=40 or
            config['runtime_deadline_seconds']!=600 or config['historical_max_per_slot']!=1 or config['historical_max_per_day']!=4 or
            config['financial_minutes'] not in ('15,45','5,15,25,35,45,55') or config['cap_minute']!=0 or
            config['cap_hours']!='0-8,10-17,19-23'):
        raise RuntimeError('Step 3 scheduled bounds changed')
    cap=json.loads((ROOT/config['cap_canary_evidence']).read_text())
    financial=json.loads((ROOT/config['financial_canary_evidence']).read_text())
    if cap['status']!='PASS' or financial['execution_status']!='PASS':
        raise RuntimeError('Step 3 recurring jobs require successful retained canaries')
    return config


def jobs():
    from apscheduler.triggers.cron import CronTrigger
    from scrapper.sources.exchange.reporting_periods import IST
    config=settings();result=[]
    if config['financial_enabled']:
        result.append(('current-financial','FINANCIAL',CronTrigger(minute=config['financial_minutes'],timezone=IST)))
    if config['cap_enabled']:
        result.append(('market-cap','CAP',CronTrigger(hour=config['cap_hours'],minute=config['cap_minute'],timezone=IST)))
    return result


def execute_once(kind,out,*,verify_only=False,company_path=None,paired_downloads=None):
    import fcntl
    import step3_financial as adapter
    from production_ownership import owner_gate
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    config=settings()
    adapter.OUT=out
    plan=adapter.prepare(scope_monitor=kind=='CAP')
    from financial_priority import historical_slot
    admission=json.loads((out/'admission.json').read_text()) if (out/'admission.json').exists() else {}
    scheduled_at=datetime.fromisoformat(admission['scheduled_at_ist']) if admission else None
    history_budget=bool(scheduled_at and scheduled_at.astimezone().date()==datetime.now().astimezone().date() and historical_slot(scheduled_at))
    plan.update(mode='scope_monitor_scheduled' if kind=='CAP' else 'priority_financial_scheduled',
        result_period=priority_result_periods()[0],queue_item_ids=[],companies=[],
        max_companies=0 if kind=='CAP' else config['financial_max_companies'],max_scope_checks=config['cap_max_checks'] if kind=='CAP' else 0,
        allow_new_companies=kind=='FINANCIAL',scheduled=True,
        priority_activated_at=config['priority_activated_at'],historical_slot=history_budget,
        historical_claims_permitted=kind=='FINANCIAL',priority_policy='three-priority-v1')
    if company_path is not None:
        if kind!='CAP' or company_path!='/company/HEG/':raise ValueError('Only the focused HEG correction is supported')
        plan.update(max_scope_checks=1,scope_company_path=company_path)
    adapter.save('plan.json',plan)
    manifest=json.loads((out/'manifest.json').read_text())
    manifest['plan_sha256']=hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()
    adapter.save('manifest.json',manifest)
    nonce=os.urandom(32)
    adapter.save('session-check.json',{'nonce':nonce.hex(),
        'file_hmac':hmac.new(nonce,(ROOT/'.screener_session').read_bytes(),hashlib.sha256).hexdigest()})
    os.chmod(out/'session-check.json',0o600)
    with (HERE/'.nse-owner.lock').open('a') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from automatic_acquisition import next_exchange_slot
        from scrapper.sources.exchange.reporting_periods import IST
        now=datetime.now(IST);slot=next_exchange_slot(now)
        adapter.nse.require(verify_only or slot is None or (slot-now).total_seconds()>config['exchange_reservation_seconds'],
            'Exchange acquisition window reserved; no Step 3 operation started')
        gate=owner_gate()
        image=json.loads(adapter.nse.checked(['docker','image','inspect',adapter.IMAGE]))[0]
        adapter.nse.require(image['Id']==adapter.IMAGE,'Existing canonical Step 3 image changed')
        volume=json.loads(adapter.nse.checked(['docker','volume','inspect',adapter.nse.VOLUME]))[0]
        adapter.nse.require(volume['Labels'].get('com.docker.compose.project')=='trading-ai','Production volume changed')
        wants_pair=(kind=='FINANCIAL' and not verify_only and config.get('paired_downloads_enabled',False)
            and (paired_downloads is True or (paired_downloads is None and now.minute in config.get('paired_download_minutes',[15,45]))))
        if wants_pair and not (HERE.parents[1]/'artifacts/automatic-acquisition/DOCUMENT_DOWNLOAD.stopped.json').exists():
            from phase4_documents import STORE,storage_preflight
            from phase4_schedule import settings as document_settings,reserve_daily_stage
            from scraper_storage_growth import account_and_refresh
            from automatic_acquisition import STATE,save as durable_save
            documents=document_settings();capacity=storage_preflight()
            growth,capacity=account_and_refresh(STORE,STATE/'scraper-storage-growth-v1.json',documents,capacity,
                reserve_bytes=documents.get('download_max_response_bytes',documents['max_response_bytes'])*config.get('paired_download_limit',5)*2)
            adapter.save('paired-storage-growth.json',growth)
            if reserve_daily_stage(out,'DOCUMENT_DOWNLOAD',datetime.now(timezone.utc),
                    download_limit=config.get('paired_download_limit',5)):
                limit=config.get('paired_download_limit',5)
                if type(limit) is not int or not 1<=limit<=30:raise ValueError('Invalid paired download limit')
                plan['paired_downloads']={'id':hashlib.sha256(str(out).encode()).hexdigest()[:24],
                    'limit':limit,'epoch':documents.get('acquisition_epoch_utc',documents['activated_at_utc']),
                    'max_response_bytes':documents.get('download_max_response_bytes',documents['max_response_bytes']),
                    'historical_fallback':documents.get('historical_download_fallback',False)}
                from phase4_schedule import enable_processing_companion
                enable_processing_companion(out,plan,documents,datetime.now(timezone.utc))
                adapter.save('plan.json',plan)
                adapter.save('paired-capacity.json',capacity)
                identity=json.loads((out/'identity.json').read_text())
                identity['targets']['documents']='/documents'
                identity['target_release_ids']={'documents':'c05.3-release-20261003'}
                adapter.save('identity.json',identity)
                compose=json.loads((out/'compose.json').read_text());service=compose['services']['current-financial-once']
                service['environment'].update(SCRAPER_DOCUMENT_STORE_PATH='/documents',DOCUMENT_REQUIRE_SENTINEL='1',
                    DOCUMENT_STORE_REQUIRE_CAPACITY_FILE='1',DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS='600')
                service['volumes'] += [
                    {'type':'bind','source':str(STORE),'target':'/documents','bind':{'create_host_path':False}},
                    {'type':'bind','source':str(STORE),'target':'/physical_documents','read_only':True,'bind':{'create_host_path':False}},
                    {'type':'bind','source':str(ROOT/'scrapper'),'target':'/document_app/scrapper','read_only':True,'bind':{'create_host_path':False}}]
                from postgresql_release import configure_document_companion
                configure_document_companion(compose,service,out)
                adapter.save('compose.json',compose)
                manifest=json.loads((out/'manifest.json').read_text())
                files=[p for p in (ROOT/'scrapper').rglob('*.py') if 'tests' not in p.relative_to(ROOT/'scrapper').parts]+list((ROOT/'scrapper/documents/download/certificates').glob('*.pem'))
                manifest['paired_application_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if '.venv' not in p.parts}
                manifest['plan_sha256']=hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()
                adapter.save('manifest.json',manifest)
                # The existing write identity is retained; publish only fresh capacity.
                durable_save(STORE/'.trading_ai_document_store_capacity.json',capacity)
        adapter.save('execution-started.json',{'owner_gate':gate,'started_at_utc':datetime.now(timezone.utc).isoformat(),
            'mode':plan['mode'],'budget':plan['max_scope_checks'] if kind=='CAP' else config['financial_max_companies'],'automatic_replay':False})
        command=['docker','compose','-f',str(out/'compose.json'),'--profile','current-financial',
            'run','--rm','--no-deps','--name','trading-ai-step3-'+kind.lower(), 'current-financial-once']
        if verify_only:command.append('--verify-only')
        with (out/('preflight.log' if verify_only else 'runtime.log')).open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        if plan.get('paired_downloads'):
            from scraper_storage_growth import batch_account
            from phase4_documents import STORE,storage_preflight
            from phase4_schedule import settings as document_settings
            from automatic_acquisition import STATE
            batch_account(STORE,STATE/'scraper-storage-growth-v1.json',document_settings(),storage_preflight())
        adapter.save('owner-after.json',owner_gate())
        return result.returncode
