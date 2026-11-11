"""Bounded local PostgreSQL and encrypted-backup health observation."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
SCRIPT=Path(__file__).resolve()
ROOT=SCRIPT.parents[2] if len(SCRIPT.parents)>2 else Path('/app')
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT)]


def backup_health(receipt,target_id,now,directory):
    if receipt.get('status')!='PASS' or receipt.get('target_id')!=target_id:
        raise RuntimeError('No successful backup for the selected target')
    finished=datetime.fromisoformat(receipt['finished_at_utc'])
    age=(now-finished).total_seconds()
    if not -300<=age<=26*3600:raise RuntimeError('Successful local backup is stale or its clock is invalid')
    archive=Path(receipt['archive'])
    if (archive.parent!=Path(directory) or archive.is_symlink() or not archive.name.endswith('.dump.age')
            or not re.fullmatch(r'[0-9a-f]{64}',receipt.get('encrypted_sha256',''))
            or not archive.is_file() or archive.stat().st_size!=receipt['encrypted_bytes']):
        raise RuntimeError('Successful encrypted archive is missing or its size changed')
    return {'status':'PASS','age_seconds':round(age),'encrypted_bytes':archive.stat().st_size}


def probe():
    from scrapper.db.database import connect,init_db
    with connect(read_only=True) as connection:
        init_db(connection)
        connection.execute('SELECT 1').fetchone()
        return {'status':'PASS','backend':'postgresql','role':connection.info.user,
                'target_id':os.environ['TRADING_AI_POSTGRESQL_TARGET_ID']}


def observe():
    from acquisition_release import workspace_root
    from postgresql_backup import load_configuration
    from postgresql_release import reader_prefix
    now=datetime.now(timezone.utc)
    try:
        target,config,destination=load_configuration()
        result=subprocess.run(['docker',*reader_prefix(),'/host/postgresql_health.py','--probe'],
            capture_output=True,text=True,timeout=90)
        if result.returncode:raise RuntimeError('Native read-only database probe failed')
        database=json.loads(result.stdout)
        if database!={'status':'PASS','backend':'postgresql','role':target['roles']['reader'],'target_id':target['target_id']}:
            raise RuntimeError('Native database probe identity differs')
        state=workspace_root(ROOT)/'artifacts/postgresql-backups'
        backup=backup_health(json.loads((state/'status.json').read_text()),target['target_id'],now,destination)
        value={'status':'PASS','observed_at_utc':now.isoformat(),'database':database,'backup':backup,'scope':config['scope']}
    except Exception as error:
        value={'status':'FAIL','observed_at_utc':now.isoformat(),'error_type':type(error).__name__,
               'reason':str(error) if isinstance(error,RuntimeError) else 'Local health inspection failed'}
    state=workspace_root(ROOT)/'artifacts/postgresql-backups';state.mkdir(parents=True,exist_ok=True)
    temporary=state/('health.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.chmod(0o600)
    os.replace(temporary,state/'health.json')
    return value


if __name__=='__main__':
    os.umask(0o077)
    if sys.argv[1:]==['--probe']:
        try:result=probe()
        except Exception:result={'status':'FAIL','reason':'Read-only native database probe failed'}
    elif not sys.argv[1:]:result=observe()
    else:raise SystemExit('Expected no arguments or --probe')
    print(json.dumps(result));raise SystemExit(0 if result['status']=='PASS' else 1)
