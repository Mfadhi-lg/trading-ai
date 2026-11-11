"""Opt-in native release wiring. No target file means the existing SQLite release."""
import hashlib
import json
from pathlib import Path
import re
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1] if len(HERE.parents)>1 else Path("/app")
ROLES={'exchange':'trading_ai_exchange','financial':'trading_ai_financial',
       'documents':'trading_ai_documents','reader':'trading_ai_reader','backup':'trading_ai_backup'}


def target():
    path=HERE/'postgresql-target.json'
    if not path.exists():return None
    value=json.loads(path.read_text())
    if (value.get('format')!=1 or value.get('database')!='trading_ai'
        or value.get('schema')!='trading_ai' or value.get('roles')!=ROLES
        or not re.fullmatch(r'sha256:[0-9a-f]{64}',value.get('image',''))
        or not re.fullmatch(r'postgres@sha256:[0-9a-f]{64}',value.get('postgresql_image',''))
        or value.get('socket_volume')!='trading-ai_postgresql-socket'
        or value.get('secrets_volume')!='trading-ai_postgresql-secrets'):
        raise RuntimeError('Unrecognized PostgreSQL release target')
    if str(uuid.UUID(value['target_id']))!=value['target_id']:
        raise RuntimeError('Invalid PostgreSQL target identity')
    return value


def configured_image(fallback):
    value=target()
    return value['image'] if value else fallback


def configured_host_runtime(python,site_packages):
    value=target()
    if value is None or 'host_python' not in value and 'host_site_packages' not in value:
        return python,site_packages
    paths=(value.get('host_python'),value.get('host_site_packages'))
    if any(not isinstance(path,str) or not Path(path).is_absolute() or '..' in Path(path).parts for path in paths):
        raise RuntimeError('Invalid native host runtime paths')
    manifest=json.loads((HERE/'automatic-acquisition-manifest.json').read_text())
    key='ops/activation/postgresql-target.json'
    if manifest['files'].get(key)!=hashlib.sha256((HERE/'postgresql-target.json').read_bytes()).hexdigest():
        raise RuntimeError('Native host runtime target is not manifest-bound')
    return paths



def source_hashes():
    application={};operators={}
    for directory,destination in (('scrapper',application),('ops/activation',operators),('ops/host',operators)):
        for path in sorted((ROOT/directory).rglob('*')):
            relative=path.relative_to(ROOT)
            if (not path.is_file() or any(part in {'tests','store','__pycache__','.venv'} for part in relative.parts)
                or path.name.startswith('test_') or path.suffix not in ('.py','.sql','.json','.pem')
                or (directory!='scrapper' and path.suffix!='.py')):continue
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError('Native release source resolves outside its root')
            destination[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
    if not application or not operators:raise RuntimeError('Native source inventory is empty')
    return application,operators


def verify():
    value=target()
    if value is None:raise RuntimeError('No native release is configured')
    application,operators=source_hashes()
    if value['application_hashes']!=application or value['operator_hashes']!=operators:
        raise RuntimeError('Native release source inventory or bytes changed')
    # The scheduler manifest pins the target file itself and all runtime source.
    manifest=json.loads((HERE/'automatic-acquisition-manifest.json').read_text())
    key='ops/activation/postgresql-target.json'
    if manifest['files'].get(key)!=hashlib.sha256((ROOT/key).read_bytes()).hexdigest():
        raise RuntimeError('Native release target is not manifest-bound')
    for name,digest in manifest['files'].items():
        if name.startswith(('scrapper/','ops/activation/')) and 'tests' not in Path(name).parts and not Path(name).name.startswith('test_'):
            path=ROOT/name
            if '..' in Path(name).parts or not path.resolve().is_relative_to(ROOT.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise RuntimeError('Native release configuration drift: '+name)
    return value


def environment(role):
    value=target()
    if value is None:raise RuntimeError('No native release is configured')
    login=ROLES[role]
    return {'TRADING_AI_DB_BACKEND':'postgresql',
        'DATABASE_URL':f'postgresql://{login}@/{value["database"]}?host=/var/run/trading-ai-postgresql',
        'TRADING_AI_POSTGRESQL_SCHEMA':value['schema'],
        'TRADING_AI_POSTGRESQL_TARGET_ID':value['target_id'],
        'PGPASSFILE':'/run/postgresql-credentials/pgpass'}


def identity(value,role):
    configured=target()
    if configured is None:return value
    return {**value,'image_id':configured['image'],'git_commit':configured['source_revision'],
        'postgresql':{'database':configured['database'],'schema':configured['schema'],
            'role':ROLES[role],'target_id':configured['target_id']}}


def configure(compose,service,role,identity_path):
    """Add a private socket and only this role's credential subdirectory."""
    value=target()
    if value is None:return
    service['image']=value['image']
    service['environment'].update(environment(role))
    volumes=compose.setdefault('volumes',{})
    volumes['native-socket']={'external':True,'name':value['socket_volume']}
    volumes['native-credentials']={'external':True,'name':value['secrets_volume']}
    service['volumes'] += [
        {'type':'volume','source':'native-socket','target':'/var/run/trading-ai-postgresql','read_only':True},
        {'type':'volume','source':'native-credentials','target':'/run/postgresql-credentials','read_only':True,
         'volume':{'subpath':'roles/'+role,'nocopy':True}}]
    # The original SQLite file is immutable to every replacement worker. Existing
    # evidence/output subdirectories retain their existing physical store.
    for mount in service['volumes']:
        if mount.get('type')=='volume' and mount.get('target')=='/data':
            mount['read_only']=True
            for directory in ('files','snapshots','exports','campaigns','logs','browser-state','backups'):
                service['volumes'].append({'type':'volume','source':mount['source'],
                    'target':'/data/'+directory,'volume':{'subpath':directory,'nocopy':True}})
            break
    path=Path(identity_path)
    path.write_text(json.dumps(identity(json.loads(path.read_text()),role),indent=2)+'\n')


def reader_prefix():
    value=target()
    if value is None:raise RuntimeError('No native release is configured')
    args=['run','--label','com.trading-ai.role=monitor-reader','--rm','--pull=never',
        '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--cpus=.5','--memory=1536m','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
        '--mount','type=volume,source=trading-ai_scraper-data,target=/data,readonly',
        '--mount','type=bind,source='+str(HERE)+',target=/activation,readonly',
        '--mount','type=bind,source='+str(ROOT/'ops/host')+',target=/host,readonly',
        '--mount','type=bind,source='+str(ROOT/'scrapper/view/render.py')+',target=/acquisition_financial_renderer.py,readonly',
        '--mount','type=volume,src='+value['socket_volume']+',dst=/var/run/trading-ai-postgresql,readonly',
        '--mount','type=volume,src='+value['secrets_volume']+',dst=/run/postgresql-credentials,volume-subpath=roles/reader,readonly',
        '--mount','type=bind,src='+str(HERE/'runtime-identity-postgresql-reader.json')+',dst=/run/trading-ai/runtime-identity.json,readonly']
    for key,item in {**environment('reader'),'TRADING_AI_ENV':'production',
        'TRADING_AI_RUNTIME_IDENTITY_FILE':'/run/trading-ai/runtime-identity.json',
        'PYTHONDONTWRITEBYTECODE':'1'}.items():args += ['--env',key+'='+item]
    return [*args,'--entrypoint','python',value['image']]


def is_reader_command(parts):
    if not parts or Path(parts[0]).name not in ('docker','com.docker.cli') or target() is None:return False
    prefix=reader_prefix()
    if parts[1:1+len(prefix)]!=prefix:return False
    tail=parts[1+len(prefix):]
    if not tail:return False
    if tail[0]=='/host/acquisition_db_status.py':return tail[1:] in ([],['--summary'])
    if tail[0] not in ('/host/acquisition_live_reader.py','/host/acquisition_queries.py') or len(tail)!=3:return False
    if tail[1] not in {'census','quarter_monitor','quarter_options','quarter_coverage_export','summary',
        'search','company','company_report','company_catalog','financials','download_diagnostics'}:return False
    try:return isinstance(json.loads(tail[2]),dict)
    except ValueError:return False


def configure_document_companion(compose,service,out):
    value=target()
    if value is None:return
    destination=Path(out)/'postgresql-documents-identity.json'
    base=json.loads((Path(out)/'identity.json').read_text())
    destination.write_text(json.dumps(identity(base,'documents'),indent=2)+'\n')
    service['volumes'].append({'type':'volume','source':'native-credentials',
        'target':'/run/postgresql-documents-credentials','read_only':True,
        'volume':{'subpath':'roles/documents','nocopy':True}})
    service['environment'].update(
        TRADING_AI_DOCUMENTS_DATABASE_URL=environment('documents')['DATABASE_URL'],
        TRADING_AI_DOCUMENTS_PGPASSFILE='/run/postgresql-documents-credentials/pgpass',
        TRADING_AI_DOCUMENTS_RUNTIME_IDENTITY_FILE='/selected/'+destination.name)
