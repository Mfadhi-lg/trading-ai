"""Document stages for the existing host dispatcher, disabled until authorized."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json,hashlib,subprocess,fcntl,os
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1] if len(HERE.parents)>1 else Path("/app")
CONFIG=HERE/'phase4_schedule.json'


def authorization_state(c, now=None):
    now=now or datetime.now(timezone.utc)
    if not c['enabled']:return 'PAUSED'
    if now<datetime.fromisoformat(c['activated_at_utc']):return 'PAUSED'
    if c.get('authorization_mode','finite')=='continuous':
        if not c.get('authorization_evidence'):
            raise RuntimeError('Continuous acquisition requires explicit authorization evidence')
        return 'RUNNING'
    return 'EXPIRED_AUTHORIZATION' if now>=datetime.fromisoformat(c['expires_at_utc']) else 'RUNNING'


def settings():
    c=json.loads(CONFIG.read_text())
    if c.get('authorization_mode','finite') not in ('finite','continuous'):
        raise RuntimeError('Unknown document authorization mode')
    if type(c.get('enforce_external_growth_limit', True)) is not bool:
        raise ValueError('Invalid storage growth enforcement setting')
    if type(c.get('concurrent_processing_enabled', False)) is not bool:
        raise ValueError('Invalid concurrent processing setting')
    if type(c.get('historical_download_fallback',False)) is not bool:
        raise ValueError('Invalid historical download fallback')
    if type(c.get('download_max_response_bytes',10485760)) is not int or not 10485760 <= c.get('download_max_response_bytes',10485760) <= 256*1024**2:
        raise ValueError('Invalid document download response limit')
    bounds={
        'historical_limit':0,'runtime_deadline_seconds':480,'max_response_bytes':10485760,
        'max_redirects':1,'max_download_attempts':3,'initial_duration_hours':24,'concurrency':1,
        'patrol_hour':2,'rating_hour':3}
    if any(c[k]!=v for k,v in bounds.items()):raise RuntimeError('Document initial scheduling bounds changed')
    for key,lo,hi,default in (('download_limit',1,30,1),('processing_limit',1,30,1),
            ('rating_daily_slots',1,4,1),('external_growth_limit_bytes',536870912,2147483648,536870912)):
        if type(c.get(key,default)) is not int or not lo <= c.get(key,default) <= hi:
            raise ValueError('Invalid routine capacity: '+key)
    for key,allowed in {'discovery_minute':(5,18),'download_minute':(25,8),'processing_minute':(35,11,13),'patrol_minute':(5,22),'rating_minute':(5,22)}.items():
        if c[key] not in allowed:raise ValueError('Unsupported safe schedule: '+key)
    if c.get('processing_minutes',str(c['processing_minute'])) not in ('35','11','11,31,51','13,33,53'):
        raise ValueError('Unsupported processing cadence')
    if c.get('download_minutes',str(c['download_minute'])) not in ('25','8','8,28,48','8,20,28,40,48'):
        raise ValueError('Unsupported download cadence')
    for key in ('download_daily_slots','processing_daily_slots'):
        maximum=168 if key=='download_daily_slots' else 240
        if type(c.get(key,24)) is not int or not 1<=c.get(key,24)<=maximum:raise ValueError('Invalid stage slot budget')
    discovery_capacity(c)
    if type(c.get('rating_browser_request_limit',12)) is not int or not 1 <= c.get('rating_browser_request_limit',12) <= 12:
        raise ValueError('Rating browser request limit must be between one and twelve')
    if c['enabled']:
        from scrapper.documents.availability import parse_utc
        start=parse_utc(c['activated_at_utc']);end=parse_utc(c['expires_at_utc'])
        if not isinstance(c['initial_free_bytes'],int):raise RuntimeError('Initial physical capacity baseline required')
        hours=c.get('verification_window_hours',24)
        if type(hours) is not int or hours not in (4,5,6,24):
            raise RuntimeError('Document window must be initial 24h or bounded 4–6h verification')
        if end-start!=timedelta(hours=hours):
            raise RuntimeError('Document window duration differs from authorized bounds')
        if parse_utc(c.get('acquisition_epoch_utc', c['activated_at_utc'])) > start:
            raise RuntimeError('Acquisition epoch cannot follow document activation')
    return c


def discovery_capacity(c):
    """Explicit per-run and daily ceilings, including manual invocations."""
    defaults = {'discovery_limit': 2, 'screener_request_limit': 2,
                'official_request_limit': 2, 'discovery_daily_slots': 4,
                'discovery_requests_max_per_day': 16}
    value = {key: c.get(key, default) for key, default in defaults.items()}
    for key, upper, lower in (('discovery_limit',100,1), ('screener_request_limit',100,1),
                             ('official_request_limit',2,0), ('discovery_daily_slots',24,1),
                             ('discovery_requests_max_per_day',1000,1)):
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise ValueError('Invalid discovery capacity: '+key)
    # Restrict schedules to explicit unique hours so capacity is reviewable.
    hours = c.get('discovery_hours','0,6,12,18').split(',')
    if (any(not hour.isdigit() or not 0 <= int(hour) <= 23 for hour in hours)
            or len(set(map(int,hours))) != len(hours)):
        raise ValueError('Discovery hours must be distinct hours from 0 to 23')
    if value['discovery_requests_max_per_day'] > c.get('source_requests_max_per_day',42):
        raise ValueError('Discovery daily requests exceed the source budget')
    return value


def jobs(now=None):
    from apscheduler.triggers.cron import CronTrigger
    from scrapper.sources.exchange.reporting_periods import IST
    c=settings();now=now or datetime.now(timezone.utc)
    if authorization_state(c,now)!='RUNNING':return []
    return [
        ('document-discovery','DOCUMENT_DISCOVERY',CronTrigger(hour=c['discovery_hours'],minute=c['discovery_minute'],timezone=IST)),
        ('document-download','DOCUMENT_DOWNLOAD',CronTrigger(minute=c.get('download_minutes',str(c['download_minute'])),timezone=IST)),
        ('document-processing','DOCUMENT_PROCESSING',CronTrigger(minute=c.get('processing_minutes',str(c['processing_minute'])),timezone=IST)),
        ('document-patrol','DOCUMENT_PATROL',CronTrigger(hour=2,minute=c['patrol_minute'],timezone=IST)),
        ('document-rating','DOCUMENT_RATING',CronTrigger(hour=c.get('rating_hours','3'),minute=c['rating_minute'],timezone=IST))]


def save(out,name,data): (out/name).write_text(json.dumps(data,indent=2,default=str)+'\n')


def reserve_daily_stage(out,kind,now,*,download_limit=None,reservation_file='daily-budget-reservation.json'):
    """Manual canaries consume the same daily capacity as scheduled stages.

    An interrupted reservation is retained. It never grants source-slot replay.
    The existing owner lock serializes admission and publication.
    """
    from automatic_acquisition import STATE, save as durable_save
    from scrapper.sources.exchange.reporting_periods import IST
    config=settings()
    if reservation_file not in ('daily-budget-reservation.json','processing-budget-reservation.json') or (
            reservation_file=='processing-budget-reservation.json' and kind!='DOCUMENT_PROCESSING'):
        raise ValueError('Invalid stage reservation file')
    if (out/reservation_file).exists():
        raise RuntimeError('Stage budget already reserved')
    selected_limit=config.get('download_limit',1) if download_limit is None else download_limit
    if download_limit is not None and (kind!='DOCUMENT_DOWNLOAD' or
            type(download_limit) is not int or not 1<=download_limit<=config.get('download_limit',1)):
        raise ValueError('Invalid bounded download reservation')
    capacity=discovery_capacity(config)
    maximum={'DOCUMENT_DISCOVERY':capacity['discovery_daily_slots'],'DOCUMENT_DOWNLOAD':config.get('download_daily_slots',24)*config.get('download_limit',1)*2,'DOCUMENT_PROCESSING':config.get('processing_daily_slots',24),
             'DOCUMENT_PATROL':1,'DOCUMENT_RATING':config.get('rating_daily_slots',1)}[kind]
    day=now.astimezone(IST).date().isoformat()
    used=0
    discovery_requests=0
    total_source_requests=0
    total_source_bytes=0
    processing_documents=0
    def source_capacity(stage,item):
        defaults={'DOCUMENT_DISCOVERY':4,'DOCUMENT_DOWNLOAD':2,'DOCUMENT_PATROL':1,'DOCUMENT_RATING':2}
        count=item.get('source_request_units') or (item.get('request_units',2) if stage=='DOCUMENT_DOWNLOAD' else defaults.get(stage,0))
        size=item.get('source_byte_units',count*(2 if stage=='DOCUMENT_DISCOVERY' else 10)*1024**2)
        return count,size
    # Download capacity is request capacity: one original plus at most one
    # policy-validated redirect. Existing one-request reservations retain cost 1.
    units=selected_limit*2 if kind=='DOCUMENT_DOWNLOAD' else 1
    for folder in STATE.glob(day+'-*'):
        if folder.resolve()==out.resolve():continue
        reservations=[p for p in (folder/'daily-budget-reservation.json',folder/'processing-budget-reservation.json') if p.exists()]
        started=folder/'execution-started.json'
        if reservations:
            for reservation in reservations:
                item=json.loads(reservation.read_text())
                used+=item.get('request_units',1) if item.get('stage')==kind else 0
                if item.get('stage')=='DOCUMENT_PROCESSING':
                    processing_documents+=item.get('document_units',config.get('processing_limit',1))
                if item.get('stage')=='DOCUMENT_DISCOVERY':
                    discovery_requests+=item.get('source_request_units',4)
                requests,byte_count=source_capacity(item.get('stage'),item)
                total_source_requests+=requests;total_source_bytes+=byte_count
        elif started.exists():
            item=json.loads(started.read_text())
            used+=item.get('stage')==kind
            if item.get('stage')=='DOCUMENT_PROCESSING':processing_documents+=config.get('processing_limit',1)
            if item.get('stage')=='DOCUMENT_DISCOVERY': discovery_requests+=4
            requests,byte_count=source_capacity(item.get('stage'),{})
            total_source_requests+=requests;total_source_bytes+=byte_count
    source_units=(capacity['screener_request_limit']+capacity['official_request_limit'] if kind=='DOCUMENT_DISCOVERY'
        else config.get('rating_browser_request_limit',12) if kind=='DOCUMENT_RATING'
        else selected_limit*2 if kind=='DOCUMENT_DOWNLOAD' else 1 if kind=='DOCUMENT_PATROL' else 0)
    source_bytes=source_units*(config.get('download_max_response_bytes',10*1024**2) if kind=='DOCUMENT_DOWNLOAD' else (2 if kind=='DOCUMENT_DISCOVERY' else 10)*1024**2)
    if used+units>maximum:return False
    document_units=config.get('processing_limit',1) if kind=='DOCUMENT_PROCESSING' else 0
    if document_units and processing_documents+document_units>config.get('extractions_max_per_day',720):return False
    if kind=='DOCUMENT_DISCOVERY' and discovery_requests+source_units>capacity['discovery_requests_max_per_day']:return False
    if source_units and total_source_requests+source_units>config.get('source_requests_max_per_day',42):return False
    if source_units and total_source_bytes+source_bytes>config.get('source_bytes_max_per_day',306184192):return False
    durable_save(out/reservation_file,{'stage':kind,'day_ist':day,
        'reserved_at_utc':now,'ordinal':used+1,'maximum':maximum,
        'request_units':units,'source_request_units':source_units,'source_byte_units':source_bytes,'document_units':document_units,
        'interrupted_reservation_consumed':True})
    return True


def enable_processing_companion(out,plan,config,now):
    """Reserve processing from the same daily budget as standalone processing."""
    if not config.get('concurrent_processing_enabled',False):return False
    from automatic_acquisition import STATE
    if (STATE/'DOCUMENT_PROCESSING.stopped.json').exists():return False
    if not reserve_daily_stage(out,'DOCUMENT_PROCESSING',now,
            reservation_file='processing-budget-reservation.json'):
        save(out,'processing-companion-admission.json',{'status':'daily_processing_budget_exhausted'})
        return False
    plan['concurrent_processing']={'limit':config['processing_limit'],
        'epoch':config.get('acquisition_epoch_utc',config['activated_at_utc']),
        'focus_company_ids':plan.get('focus_membership',{}).get('company_ids',[])}
    save(out,'plan.json',plan)
    manifest=json.loads((out/'manifest.json').read_text())
    manifest['plan_sha256']=hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()
    save(out,'manifest.json',manifest)
    return True


def cleanup_timed_out_container(out):
    """Verify ownership and termination; a failure before creation is not a stop failure."""
    name='trading-ai-phase4-routine'
    checked=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=30)
    if checked.returncode:
        if 'No such object:' not in checked.stderr and 'No such container:' not in checked.stderr:
            raise RuntimeError('Cannot verify timed-out document container: '+checked.stderr.strip())
        save(out,'timeout-cleanup.json',{'container':name,'status':'not_created','terminated':True})
        return
    row=json.loads(checked.stdout)[0]
    labels=row['Config'].get('Labels') or {}
    if labels.get('com.docker.compose.project')!='trading-ai-phase4-routine' or labels.get('com.docker.compose.service')!='document-once':
        raise RuntimeError('Timed-out document container ownership differs')
    if row['State']['Status']=='paused':
        subprocess.run(['docker','unpause',row['Id']],check=True,timeout=30)
    if row['State']['Status'] in ('running','restarting','paused'):
        subprocess.run(['docker','stop','-t','15',row['Id']],check=True,timeout=30)
    after=subprocess.run(['docker','inspect',row['Id']],capture_output=True,text=True,timeout=30)
    if after.returncode:
        if 'No such object:' not in after.stderr and 'No such container:' not in after.stderr:
            raise RuntimeError('Cannot verify document container termination')
    elif json.loads(after.stdout)[0]['State']['Status'] not in ('exited','dead','created'):
        raise RuntimeError('Timed-out document container still active')
    else:
        # A run that never started may retain its name. Never force-remove or
        # remove volumes; Docker refuses this if it became running meanwhile.
        subprocess.run(['docker','rm',row['Id']],check=True,timeout=30)
    save(out,'timeout-cleanup.json',{'container':name,'container_id':row['Id'],'status':'terminated','terminated':True})


def prepare(out,kind,*,authorized=False,local_historical_processing=False,recovery_campaign=None,saved_parse_ids=()):
    from phase4_documents import IMAGE,STORE
    c=settings()
    out.mkdir(parents=True,exist_ok=True)
    plan={**c,'stage':kind,'epoch':c.get('acquisition_epoch_utc',c['activated_at_utc']),'authorized':authorized}
    if saved_parse_ids:
        if kind!='DOCUMENT_DOWNLOAD' or len(saved_parse_ids)>8:raise ValueError('Bounded saved-page download repair only')
        plan['saved_link_repair_ids']=list(saved_parse_ids)
    if recovery_campaign is not None:
        if kind!='DOCUMENT_DOWNLOAD':raise ValueError('Finite download campaign is download-only')
        plan['recovery_campaign']=recovery_campaign
    if local_historical_processing:
        if kind!='DOCUMENT_PROCESSING':raise ValueError('Historical campaign is local processing only')
        plan.update(local_historical_processing_limit=1,local_campaign_id='acquisition-recovery-20261026-one-local-document')
    from reporting_focus import membership
    from scrapper.sources.exchange.reporting_periods import priority_result_periods
    plan['focus_membership']=membership(ROOT/'artifacts/focus_ml/governance/FOCUS_LABEL_REGISTRY_v001.json',priority_result_periods()[0])
    save(out,'plan.json',plan)
    identity=json.loads((HERE/'runtime-identity-nse-results.json').read_text())
    identity.update(authorized=authorized,image_id=IMAGE)
    identity['targets']['documents']='/documents'
    # The retained external archive keeps its original physical identity.
    # Verify that exact release instead of hiding the marker with an overlay.
    identity['target_release_ids']={'documents':'c05.3-release-20261003'}
    save(out,'runtime-identity.json',identity)
    doc={'environment':'production','path':'/documents','release_id':identity['release_id'],'roles':['documents']}
    save(out,'documents-sentinel.json',doc)
    compose=json.loads((HERE/'compose.nse.json').read_text());service=compose['services'].pop('nse-results')
    compose['name']='trading-ai-phase4-routine';compose['services']={'document-once':service}
    service.update(image=IMAGE,profiles=['documents'],entrypoint=['python','/activation/phase4_routine_runtime.py'],stop_grace_period='15s')
    if kind=='DOCUMENT_PROCESSING':service['network_mode']='none'
    for m in service['volumes']:
        if m['type']=='bind':m['source']=str(HERE)
    service['volumes'] += [
        {'type':'bind','source':str(ROOT/'scrapper'),'target':'/app/scrapper','read_only':True,'bind':{'create_host_path':False}},
        {'type':'bind','source':str(out),'target':'/selected','read_only':True},
        {'type':'bind','source':str(STORE),'target':'/documents','bind':{'create_host_path':False}},
        {'type':'bind','source':str(STORE),'target':'/physical_documents','read_only':True,'bind':{'create_host_path':False}},
        {'type':'bind','source':str(ROOT/'.screener_session'),'target':'/run/secrets/screener-session','read_only':True,'bind':{'create_host_path':False}}]
    service['environment'].update(SCRAPER_DOCUMENT_STORE_PATH='/documents',PYTHONDONTWRITEBYTECODE='1',
        SCREENER_SESSION_FILE='/run/secrets/screener-session',DOCUMENT_REQUIRE_SENTINEL='1',
        DOCUMENT_STORE_REQUIRE_CAPACITY_FILE='1',DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS='600')
    compose['configs']['nse_identity']['file']=str(out/'runtime-identity.json')
    for name in ('nse_store','nse_snapshots'):compose['configs'][name]['file']=str((HERE/compose['configs'][name]['file']).resolve())
    # Physical document marker remains visible and independently hash-checked.
    from postgresql_release import configure
    configure(compose,service,'documents',out/'runtime-identity.json')
    save(out,'compose.json',compose)
    # Runtime attestation covers application code, not concurrently edited tests.
    files=[p for p in (ROOT/'scrapper').rglob('*.py') if 'tests' not in p.relative_to(ROOT/'scrapper').parts]+list((ROOT/'scrapper/documents/download/certificates').glob('*.pem'))
    save(out,'manifest.json',{'image':IMAGE,'application_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if '.venv' not in p.parts},
        'plan_sha256':hashlib.sha256((out/'plan.json').read_bytes()).hexdigest(),
        'runtime_sha256':hashlib.sha256((HERE/'phase4_routine_runtime.py').read_bytes()).hexdigest()})
    return plan


def execute_once(kind,out,*,local_historical_processing=False,resume_local_campaign=False,recovery_campaign=None,saved_parse_ids=()):
    from production_ownership import owner_gate
    from phase4_documents import storage_preflight,STORE,IMAGE
    from scrapper.sources.exchange.reporting_periods import IST
    import time
    started=time.monotonic();checkpoint=started;timings={}
    def timing(name):
        nonlocal checkpoint
        current=time.monotonic();timings[name]=round(current-checkpoint,6);checkpoint=current
        save(out,'host-timings.json',{'timings':timings,'total_seconds':round(current-started,6)})
    c=settings();now=datetime.now(timezone.utc)
    if not jobs(now):raise RuntimeError('Document scheduling disabled or outside authorized window')
    timing('configuration_and_admission')
    # The same finite owner lock used by Phase3, no new daemon or lock domain.
    with (HERE/'.nse-owner.lock').open('a') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        local=now.astimezone(IST)
        from automatic_acquisition import next_exchange_slot
        upcoming=next_exchange_slot(local)
        if upcoming is not None and (upcoming-local).total_seconds()<600:
            raise BlockingIOError('Exchange acquisition window reserved')
        gate=owner_gate()
        timing('ownership_checks')
        if local_historical_processing:
            from automatic_acquisition import STATE
            campaign=STATE/'local-historical-processing-20261026.json'
            if campaign.exists() and not resume_local_campaign:
                raise RuntimeError('One-document local historical campaign already reserved; inspect durable work before further recovery')
            if resume_local_campaign and not campaign.exists():raise RuntimeError('No retained local campaign to resume')
        if recovery_campaign is not None:
            if kind!='DOCUMENT_DOWNLOAD' or not recovery_campaign.get('authorization_reference'):
                raise ValueError('Explicit finite campaign authorization required')
            campaign_jobs=recovery_campaign['jobs']
            if len(campaign_jobs)!=1:raise ValueError('Each campaign slot reserves exactly one request')
            from automatic_acquisition import STATE,save as durable_save
            cid=hashlib.sha256(recovery_campaign['campaign_id'].encode()).hexdigest()
            reservation=STATE/('finite-download-'+cid+'-'+str(campaign_jobs[0]['download_job_id'])+'.json')
            if reservation.exists():raise RuntimeError('Campaign request already reserved; no automatic replay')
        prepare(out,kind,authorized=True,local_historical_processing=local_historical_processing,recovery_campaign=recovery_campaign,saved_parse_ids=saved_parse_ids)
        if resume_local_campaign:
            previous=json.loads(campaign.read_text())
            if previous.get('maximum_documents')!=1 or previous.get('network_mode')!='none':raise RuntimeError('Local campaign bounds changed')
            for key in ('resume_work_id','resume_content_id'):
                if type(previous.get(key)) is not int or previous[key]<1:raise RuntimeError('Exact interrupted work identity required')
            plan=json.loads((out/'plan.json').read_text())
            plan.update(local_resume_work_id=previous['resume_work_id'],local_resume_content_id=previous['resume_content_id'])
            save(out,'plan.json',plan)
            manifest=json.loads((out/'manifest.json').read_text());manifest['plan_sha256']=hashlib.sha256((out/'plan.json').read_bytes()).hexdigest()
            save(out,'manifest.json',manifest)
        timing('package_attestation_and_compose_preparation')
        capacity=storage_preflight();save(out,'execution-storage-preflight.json',capacity)
        timing('physical_storage_preflight')
        from scraper_storage_growth import batch_account,account_and_refresh
        from automatic_acquisition import STATE
        growth,capacity=account_and_refresh(STORE,STATE/'scraper-storage-growth-v1.json',c,capacity,
            reserve_bytes=(c.get('download_max_response_bytes',c['max_response_bytes']) if kind=='DOCUMENT_DOWNLOAD' else c['max_response_bytes'])*((discovery_capacity(c)['screener_request_limit']+c['official_request_limit']) if kind=='DOCUMENT_DISCOVERY' else c['download_limit']*2 if kind=='DOCUMENT_DOWNLOAD' else c.get('rating_browser_request_limit',12) if kind=='DOCUMENT_RATING' else 2))
        save(out,'execution-storage-preflight.json',capacity)
        save(out,'scraper-storage-growth.json',growth)
        timing('storage_growth_inventory_and_capacity_refresh')
        if capacity['free_bytes']-capacity['required_free_bytes']<c['max_response_bytes']*2:raise RuntimeError('Reserve cannot cover document slot')
        image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]
        if image['Id']!=IMAGE:raise RuntimeError('Document image changed')
        timing('docker_image_check')
        if not reserve_daily_stage(out,kind,now):
            save(out,'budget-exhausted.json',{'stage':kind,'reason':'Daily scheduled and manual capacity exhausted'})
            return 78
        if kind=='DOCUMENT_DOWNLOAD' and recovery_campaign is None:
            enable_processing_companion(out,json.loads((out/'plan.json').read_text()),c,now)
        if recovery_campaign is not None:
            durable_save(reservation,{'campaign':recovery_campaign,'receipt':str(out),'reserved_at_utc':now,
                'interrupted_reservation_consumed':True})
        if local_historical_processing and not resume_local_campaign:
            from automatic_acquisition import save as durable_save
            durable_save(STATE/'local-historical-processing-20261026.json',{
                'campaign_id':'acquisition-recovery-20261026-one-local-document','maximum_documents':1,
                'network_mode':'none','external_requests':0,'started_at_utc':now,'receipt':str(out),
                'authorization':'26 October recovery instruction, Phase E: separately controlled bounded local historical processing'})
        save(out,'execution-started.json',{'at':now,'owner':gate,'stage':kind})
        # Reuse the canary's durable capacity publication, without touching identity.
        temp=STORE/'.phase4-routine-capacity.tmp'
        with temp.open('x') as stream:
            json.dump(capacity,stream);stream.flush();os.fsync(stream.fileno())
        temp.replace(STORE/'.trading_ai_document_store_capacity.json')
        args=['docker','compose','-f',str(out/'compose.json'),'--profile','documents','run','--rm','--no-deps',
            '--name','trading-ai-phase4-routine','document-once']
        try:
            with (out/'runtime.log').open('w') as log:
                result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=495)
            timing('container_start_execution_and_shutdown')
            if result.returncode and not any('"stage":' in line for line in (out/'runtime.log').read_text().splitlines()):
                log=(out/'runtime.log').read_text()
                if any(marker in log for marker in ('Cannot connect to the Docker daemon','Cannot connect to Docker','docker API','Is the docker daemon running')):
                    return 75
            batch_account(STORE,STATE/'scraper-storage-growth-v1.json',c,storage_preflight())
            return result.returncode
        except subprocess.TimeoutExpired:
            # A killed compose client does not stop its container. Stop the
            # named owned container before releasing host ownership.
            cleanup_timed_out_container(out)
            timing('timeout_and_verified_cleanup')
            return 75
