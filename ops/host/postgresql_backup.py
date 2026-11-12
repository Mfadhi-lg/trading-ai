"""Explicit local encrypted PostgreSQL backup; source access is read-only."""
import argparse
from datetime import datetime,timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT)]


def load_configuration():
    from postgresql_release import verify
    target=verify()
    config=json.loads((ROOT/'ops/activation/postgresql-backup.json').read_text())
    destination=Path(config['directory'])
    if (not destination.is_absolute() or destination.is_symlink()
        or not re.fullmatch(r'age1[0-9a-z]+',config.get('recipient',''))
        or config.get('scope')!='local_only_owner_selected'
        or config.get('target_id')!=target['target_id']):
        raise RuntimeError('Unrecognized local PostgreSQL backup configuration')
    marker=json.loads((destination/'.trading-ai-postgresql-backups.json').read_text())
    if marker!={'application':'trading-ai','target_id':target['target_id'],'scope':'local_only_owner_selected'}:
        raise RuntimeError('Backup storage identity mismatch')
    if shutil.disk_usage(destination).free < 100*1024**3:
        raise RuntimeError('Backup drive has less than the retained 100 GiB reserve')
    binary=Path(config['age_binary'])
    if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest()!=config['age_sha256']:
        raise RuntimeError('Reviewed backup encryption executable changed')
    return target,config,destination


def dump_command(target,name):
    if not re.fullmatch(r"trading-ai-postgresql-backup-[0-9a-f]{32}",name):
        raise ValueError("Invalid owned backup container name")
    return ['docker','run','--name',name,'--rm','--pull=never','--network=none','--read-only',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--user','70:70',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
        '--mount','type=volume,src='+target['socket_volume']+',dst=/var/run/trading-ai-postgresql,readonly',
        '--mount','type=volume,src='+target['secrets_volume']+',dst=/run/postgresql-credentials,volume-subpath=roles/backup,readonly',
        '--env','PGHOST=/var/run/trading-ai-postgresql','--env','PGPORT=5432',
        '--env','PGDATABASE='+target['database'],'--env','PGUSER='+target['roles']['backup'],
        '--env','PGPASSFILE=/run/postgresql-credentials/pgpass','--entrypoint','pg_dump',
        target['postgresql_image'],'--format=custom','--no-owner','--no-privileges','--schema='+target['schema']]



def cleanup_owned_dump(name,processes):
    if not re.fullmatch(r'trading-ai-postgresql-backup-[0-9a-f]{32}',name):
        raise ValueError('Refusing to stop an unowned backup client')
    try:
        for process in processes:
            if process is not None and process.poll() is None:
                process.kill();process.wait(timeout=10)
    finally:
        cleanup=subprocess.run(['docker','stop','--time','10',name],capture_output=True,timeout=30)
        if cleanup.returncode:
            check=subprocess.run(['docker','container','inspect',name],capture_output=True,timeout=30)
            if check.returncode==0 or b'No such container' not in check.stderr:
                raise RuntimeError('Owned backup client cleanup could not be verified')


def backup():
    from acquisition_release import workspace_root
    os.umask(0o077);started=time.monotonic()
    status=workspace_root(ROOT)/'artifacts/postgresql-backups/status.json'
    status.parent.mkdir(parents=True,exist_ok=True)
    def save(value):
        temporary=status.with_name('status.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(value,indent=2)+'\n');os.replace(temporary,status)
    gate=(status.parent/'backup.lock').open('a')
    fcntl.flock(gate,fcntl.LOCK_EX|fcntl.LOCK_NB)
    target={};path=None
    try:
        target,config,destination=load_configuration()
    except BaseException as error:
        save({'status':'FAIL','error_type':type(error).__name__,'failed_at_utc':datetime.now(timezone.utc).isoformat()})
        gate.close();raise
    job_id=uuid.uuid4().hex
    container='trading-ai-postgresql-backup-'+job_id
    now=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+job_id
    path=destination/(now+'.dump.age.partial')
    save({'status':'RUNNING','phase':'dump_and_encrypt','started_at_utc':datetime.now(timezone.utc).isoformat(),
          'target_id':target['target_id'],'scope':config['scope']})
    dump=encrypted=None
    try:
        # Native errors can contain SQL. Keep diagnostics private, never print them.
        with (destination/(now+'.private.log')).open('xb') as diagnostics:
            dump=subprocess.Popen(dump_command(target,container),stdout=subprocess.PIPE,stderr=diagnostics)
            encrypted=subprocess.Popen([config['age_binary'],'-r',config['recipient'],'-o',str(path)],
                stdin=dump.stdout,stdout=subprocess.DEVNULL,stderr=diagnostics)
            dump.stdout.close()
            encryption_status=encrypted.wait(timeout=4*3600)
            dump_status=dump.wait(timeout=60)
        if dump_status or encryption_status:raise RuntimeError('Local encrypted backup failed; private diagnostics retained')
        path.chmod(0o600)
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        complete=path.with_suffix('');os.link(path,complete);path.unlink();path=complete
        result={'status':'PASS','finished_at_utc':datetime.now(timezone.utc).isoformat(),
            'archive':str(complete),'encrypted_sha256':digest.hexdigest(),'encrypted_bytes':complete.stat().st_size,
            'elapsed_seconds':round(time.monotonic()-started,3),'target_id':target['target_id'],'scope':config['scope']}
        receipt=complete.with_suffix(complete.suffix+'.json');receipt.write_text(json.dumps(result,indent=2)+'\n')
        save(result);return result
    except BaseException as error:
        cleanup_error=None
        try:cleanup_owned_dump(container,(encrypted,dump))
        except BaseException as failure:cleanup_error=type(failure).__name__
        save({'status':'FAIL','error_type':type(error).__name__,'target_id':target['target_id'],
              'failed_at_utc':datetime.now(timezone.utc).isoformat(),'partial_archive':str(path),
              'owned_dump_container':container,'cleanup_verified':cleanup_error is None,
              'cleanup_error_type':cleanup_error})
        if cleanup_error:raise RuntimeError('Owned backup client cleanup could not be verified') from None
        raise
    finally:
        gate.close()


def main(arguments=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(arguments)
    if not args.execute:
        load_configuration();print(json.dumps({'status':'CONFIGURATION_VERIFIED','database_access':False}));return 0
    previous=signal.getsignal(signal.SIGTERM)
    def interrupted(*_):raise KeyboardInterrupt('Backup termination requested')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        result=backup();print(json.dumps(result));return 0
    except BaseException as error:
        print(json.dumps({'status':'FAIL','error_type':type(error).__name__}),file=sys.stderr);return 1
    finally:
        signal.signal(signal.SIGTERM,previous)

if __name__=='__main__':raise SystemExit(main())
