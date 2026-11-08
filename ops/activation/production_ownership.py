"""Production ownership evidence, without executable-name exemptions."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess

VOLUME='trading-ai_scraper-data'
ROOTS=('/data','/var/lib/docker/volumes/'+VOLUME+'/_data')
ROLES=re.compile(r'(?:^|[\s/])(exchange-scheduler|exchange-discover-results|exchange-refresh-universe|run-acquisition-role|screener-company-worker|screener-worker|document-download-worker|artifact-reconciler|credit-rating-worker|rating-recovery-download|bse_retained_runtime\.py|bse_scheduled_runtime\.py|nse_results_runtime\.py)(?:[\s:]|$)')
HOST_RUNNERS=re.compile(r'(?:^|[\s/])(bse_retained|nse_results)\.py(?:\s|$)')


def prod_file(name):
    name=name.removesuffix(' (deleted)')
    return any(name==root+'/scraper.db' or name.startswith(root+'/scraper.db-') or
               name.startswith(root+'/scraper.db.exchange-') for root in ROOTS)


def acquisition_role(args):
    return bool(ROLES.search(args) or (HOST_RUNNERS.search(args) and '--execute' in args))


def read_only_docker_command(args):
    """Exact observer commands, never an exemption for an executable name."""
    try:parts=shlex.split(args)
    except ValueError:return False
    if not parts or Path(parts[0]).name not in ('docker','com.docker.cli'):return False
    if parts[1:]==['volume','inspect',VOLUME]:return True
    if 'com.trading-ai.role=monitor-reader' not in parts:return False
    from phase4_documents import IMAGE
    from acquisition_release import workspace_root
    root=workspace_root(Path(__file__).resolve().parents[2])
    expected=['run','--label','com.trading-ai.role=monitor-reader','--rm','--pull=never','--network=none','--read-only',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=.5','--memory=1536m',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
        '--mount','type=volume,source='+VOLUME+',target=/data',
        '--mount','type=bind,source='+str(root/'ops/activation')+',target=/activation,readonly',
        '--mount','type=bind,source='+str(root/'ops/host')+',target=/host,readonly',
        '--mount','type=bind,source='+str(root/'scrapper/view/render.py')+',target=/acquisition_financial_renderer.py,readonly',
        '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,'/host/acquisition_live_reader.py']
    return parts[1:1+len(expected)]==expected and len(parts)>len(expected)+1 and parts[1+len(expected)] in {
        'census','quarter_monitor','quarter_options','quarter_coverage_export','summary','search',
        'company','company_report','company_catalog','financials','download_diagnostics'}


def classify_process(process, handles=(), environment=None):
    """Use exact targets, handles and role; cwd alone is never ownership."""
    environment=environment or {}
    relevant=[f for f in handles if prod_file(f.get('name',''))]
    for f in relevant:
        if f.get('lock') and f['lock'].strip():
            return 'conflict','held production file/ownership lock'
        if f.get('access') in ('w','u'):
            return 'conflict','writable production database handle'
        if f.get('access')!='r':
            return 'ambiguous','production handle access mode unavailable'
    args=process['args']
    if read_only_docker_command(args):
        return 'unrelated','exact read-only Docker inspection or sealed monitor transport'
    role=acquisition_role(args)
    db=environment.get('SCRAPER_DB_PATH')
    if not db and environment.get('SCRAPER_STORE_PATH'):
        db=environment['SCRAPER_STORE_PATH'].rstrip('/')+'/scraper.db'
    target=bool((db and prod_file(db)) or VOLUME in args or
                any(root+'/scraper.db' in args for root in ROOTS))
    if role and (target or environment.get('TRADING_AI_ENV')=='production'):
        return 'conflict','production acquisition role/target'
    if role:
        if environment.get('TRADING_AI_ENV') in ('development','test') and db and not prod_file(db):
            return 'unrelated','explicitly isolated development/test acquisition'
        return 'ambiguous','acquisition role without a verified isolated or production target'
    if target and not relevant:
        return 'ambiguous','production target in arguments without a verified process role'
    return 'unrelated','no production writer handle or acquisition role'


def monitor_reader(row):
    """Recognize only the sealed SQLite mode=ro reader; sidecars need RW dir."""
    config=row.get('Config',{});host=row.get('HostConfig',{})
    if (config.get('Labels') or {}).get('com.trading-ai.role')!='monitor-reader':return False
    from phase4_documents import IMAGE
    if row.get('Image')!=IMAGE or not host.get('ReadonlyRootfs') or host.get('NetworkMode')!='none':return False
    if host.get('Privileged') or host.get('CapAdd') or 'ALL' not in host.get('CapDrop',[]):return False
    if 'no-new-privileges' not in ' '.join(host.get('SecurityOpt',[])):return False
    args=row.get('Args',[])
    if row.get('Path')!='python' or len(args)!=3 or args[0]!='/host/acquisition_live_reader.py':return False
    if args[1] not in {'census','quarter_monitor','quarter_options','quarter_coverage_export','summary',
                       'search','company','company_report','company_catalog','financials','download_diagnostics'}:return False
    from acquisition_release import workspace_root
    root=workspace_root(Path(__file__).resolve().parents[2])
    mounts=row.get('Mounts',[])
    data=[m for m in mounts if m.get('Destination')=='/data']
    if len(data)!=1 or data[0].get('Name')!=VOLUME:return False
    required={'/host':str(root/'ops/host'),'/activation':str(root/'ops/activation'),
              '/acquisition_financial_renderer.py':str(root/'scrapper/view/render.py')}
    for target,source in required.items():
        found=[m for m in mounts if m.get('Destination')==target]
        if len(found)!=1 or found[0].get('RW') is not False or found[0].get('Source','').removeprefix('/host_mnt')!=source:return False
    if any(m.get('Destination') not in {*required,'/data','/tmp'} for m in mounts):return False
    return True


def container_conflict(row):
    if row['State']['Status'] in ('exited','created','dead'):
        return None
    if monitor_reader(row):return None
    mounts=[m for m in row.get('Mounts',[]) if m.get('Name')==VOLUME or
            any(m.get('Source','')==root or m.get('Source','').startswith(root+'/') for root in ROOTS[1:])]
    if any(m.get('RW') is not False for m in mounts):
        return 'active writable production mount'
    args=' '.join([row.get('Path',''), *row.get('Args',[])])
    if mounts and acquisition_role(args):
        return 'active production acquisition owner, including read-only mount'
    return None


def parse_lsof(output):
    by_pid={}
    pid=None
    current=None
    for line in output.splitlines():
        if not line:
            continue
        field,value=line[0],line[1:]
        if field=='p':
            pid=int(value); by_pid.setdefault(pid,[]); current=None
        elif field=='f' and pid is not None:
            current={'fd':value}; by_pid[pid].append(current)
        elif current is not None and field in ('a','n','l'):
            current[{'a':'access','n':'name','l':'lock'}[field]]=value
    return by_pid


def assert_no_conflicting_leases(conn, *, now):
    """Read existing acquisition lease columns; never expire or clear a lease."""
    instant=datetime.fromisoformat(now)
    if instant.tzinfo is None:
        raise RuntimeError('Ownership clock must be timezone aware')
    checked=[]
    for table in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND (sql LIKE '%lease_%' OR sql LIKE '%claim_expires%')").fetchall():
        name=table['name']
        columns=[r['name'] for r in conn.execute('PRAGMA table_info("'+name+'")')]
        for expiry in ('lease_until_utc','lease_expires_at_utc','claim_expires_at_utc'):
            if expiry not in columns:
                continue
            state_columns=[c for c in ('state','status') if c in columns]
            selected=[expiry,*state_columns]
            for row in conn.execute('SELECT '+','.join('"'+c+'"' for c in selected)+' FROM "'+name+'" WHERE "'+expiry+'" IS NOT NULL'):
                if any(str(row[c]).upper() in ('DONE','FAILED','COMPLETE','COMPLETED','RELEASED','CANCELLED','RESOLVED','ARCHIVED') for c in state_columns):
                    continue
                try:
                    until=datetime.fromisoformat(row[0])
                    if until.tzinfo is None:
                        raise ValueError('naive lease')
                except ValueError as exc:
                    raise RuntimeError('Ambiguous lease timestamp in '+name) from exc
                if until>instant:
                    raise RuntimeError('Unexpired production acquisition lease in '+name)
            checked.append(name+'.'+expiry)
    return checked


def _run(args, *, timeout=30):
    return subprocess.run(args,capture_output=True,text=True,timeout=timeout)


def owner_gate():
    def output(args):
        r=_run(args)
        if r.returncode:
            raise RuntimeError('Cannot inspect ownership: '+args[0])
        return r.stdout
    ids=output(['docker','ps','-aq']).split()
    containers=json.loads(output(['docker','inspect',*ids])) if ids else []
    for row in containers:
        conflict=container_conflict(row)
        if conflict:
            raise RuntimeError(conflict+': '+row['Name'])
    processes={}
    for line in output(['ps','-axo','pid=,ppid=,comm=']).splitlines():
        fields=line.strip().split(None,2)
        if len(fields)==3:
            processes[int(fields[0])]={'pid':int(fields[0]),'ppid':int(fields[1]),'executable':fields[2],'args':''}
    for line in output(['ps','-ww','-axo','pid=,args=']).splitlines():
        fields=line.strip().split(None,1)
        if len(fields)==2 and int(fields[0]) in processes:
            processes[int(fields[0])]['args']=fields[1]
    listing=_run(['lsof','-nP','-Fpcfanlt'])
    if listing.returncode not in (0,1) or not listing.stdout:
        raise RuntimeError('Cannot inspect actual process file handles')
    handles=parse_lsof(listing.stdout)
    inspected=[]
    self_pid=os.getpid()
    ancestors=set()
    parent=processes.get(self_pid,{}).get('ppid')
    while parent in processes and parent not in ancestors:
        ancestors.add(parent); parent=processes[parent]['ppid']
    for pid,p in processes.items():
        if pid==self_pid:
            continue
        env={}
        role=acquisition_role(p['args'])
        if role:
            # Read only relevant environment keys; never retain or emit secrets.
            full=output(['ps','eww','-p',str(pid),'-o','command='])
            for key in ('TRADING_AI_ENV','SCRAPER_DB_PATH','SCRAPER_STORE_PATH'):
                match=re.search(r'(?:^|\s)'+key+r'=([^\s]+)',full)
                if match:
                    env[key]=match.group(1)
        verdict,reason=classify_process(p,handles.get(pid,()),env)
        # An ancestor shell launching this very invocation is not a duplicate
        # owner. Never exempt it if an actual production handle is present.
        if (pid in ancestors and Path(p['executable']).name in ('zsh','bash','sh') and
                role and not any(prod_file(f.get('name','')) for f in handles.get(pid,()))):
            verdict,reason='unrelated','ancestor shell for selected operation'
        if verdict!='unrelated':
            raise RuntimeError('Host production ownership '+verdict+' PID '+str(pid)+': '+reason)
        if role or '/trading-ai' in p['args'] or '/ChatGPT.app/' in p['executable']:
            inspected.append({'pid':pid,'executable':p['executable'],'verdict':verdict,
                'reason':reason,'production_handles':[f for f in handles.get(pid,()) if prod_file(f.get('name',''))]})
    cron=_run(['crontab','-l'],timeout=10)
    if not (cron.returncode==0 or (cron.returncode==1 and 'no crontab' in cron.stderr)):
        raise RuntimeError('Cannot inspect cron owners')
    for line in cron.stdout.splitlines():
        if line.strip() and not line.lstrip().startswith('#') and acquisition_role(line):
            env=dict(re.findall(r'(TRADING_AI_ENV|SCRAPER_DB_PATH|SCRAPER_STORE_PATH)=([^\s]+)',line))
            verdict,_=classify_process({'args':line},environment=env)
            if verdict!='unrelated':
                raise RuntimeError('Competing production/ambiguous acquisition cron owner')
    known_labels=set()
    for folder in (Path('/Library/LaunchAgents'),Path('/Library/LaunchDaemons'),Path.home()/'Library/LaunchAgents'):
        if not folder.exists():
            continue
        for path in folder.glob('*.plist'):
            payload=plistlib.loads(path.read_bytes().lstrip())
            label=payload.get('Label',''); known_labels.add(label)
            if label == 'com.trading-ai.automatic-acquisition':
                from automatic_acquisition import launchagent, verify_config
                if payload != launchagent():
                    raise RuntimeError('Acquisition-only schedule configuration drift: '+str(path))
                verify_config()
                # This dispatcher admits only bounded adapters sharing the
                # host operation lock and existing per-DB ownership locks.
                continue
            args=' '.join(payload.get('ProgramArguments',[]))
            if acquisition_role(args):
                verdict,_=classify_process({'args':args},environment=payload.get('EnvironmentVariables',{}))
                if verdict!='unrelated':
                    raise RuntimeError('Scheduled production/ambiguous acquisition owner: '+str(path))
    for line in output(['launchctl','list']).splitlines():
        label=line.split()[-1] if line.split() else ''
        if label not in known_labels and re.search(r'(exchange[-_.]scheduler|screener[-_.]worker|acquisition[-_.]worker)',label,re.I):
            raise RuntimeError('Loaded acquisition owner without inspectable configuration: '+label)
    return {'status':'PASS','observed_at':datetime.now(timezone.utc).isoformat(),
        'scope':'exact production mounts, writable DB handles, acquisition roles, schedulers; runtime OS locks and leases checked separately',
        'inspected_processes':inspected}
