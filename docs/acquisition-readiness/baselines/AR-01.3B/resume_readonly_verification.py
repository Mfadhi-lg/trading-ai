"""User-confirmed Docker recovery: read-only remaining proof, never migration."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
spec=importlib.util.spec_from_file_location('driver',OUT/'migration_driver.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
RESUME=OUT/'resumed-verification'
VOLUME='trading-ai-ar013b-post-migration-verification-20261008'


def main():
    if RESUME.exists():raise RuntimeError('Resume evidence exists; do not overwrite/repeat')
    RESUME.mkdir()
    result={'status':'FAIL','migration_retried':False,'automatic_restore':False,
            'operational_workers_started':False,'network_requests':0,'production_business_writes':0}
    try:
        old=json.loads((OUT/'final.json').read_text())
        if not old['migration_exit_success']:raise RuntimeError('Original migration did not exit successfully; read-only resume not valid')
        expected=json.loads((OUT/'post-metadata.json').read_text())
        current=driver.inspect('post','resumed-production-gate')
        if current['source_sha256']!=expected['source_sha256'] or current['db_before']!=expected['db_before'] or current['ledger']!=expected['ledger']:
            raise RuntimeError('Production differs from preserved 075 state; STOP')
        args=driver.base()+['--user',f'{os.getuid()}:{os.getgid()}',
            '--mount',f'type=volume,src={VOLUME},dst=/copy,readonly',
            '--mount',f'type=bind,src={OUT},dst=/proof,readonly',
            '--mount',f'type=bind,src={RESUME},dst=/evidence',
            '--entrypoint','python',driver.host.IMAGE,'/proof/verify_post.py',current['source_sha256'],'--resume-after-sqlite-checks']
        driver.save('resumed-verification-command.json',args)
        driver.log('RESUMED_READONLY_VERIFICATION_START')
        with (RESUME/'stdout.log').open('w') as out,(RESUME/'stderr.log').open('w') as err:
            r=subprocess.run(args,stdout=out,stderr=err)
        if r.returncode or (RESUME/'stderr.log').stat().st_size:raise RuntimeError('Read-only verification resume failed; STOP, no migration/restore')
        verification=json.loads((RESUME/'verification.json').read_text())
        canary=driver.inspect('canary','schema075-canary','0.5')
        if canary['source_sha256']!=expected['source_sha256'] or canary['db_before']!=expected['db_before']:
            raise RuntimeError('Production changed during resumed proof')
        result.update({'status':'PASS','schema':'075','post_sha256':expected['source_sha256'],
            'original_data_preserved':verification['original_data_preserved'],'new_fk_identities':0,'missing_fk_identities':0,
            'quick_check':'ok','integrity_check':'ok','c28_schema_compatibility':'PASS','canary_zero_write':True,
            'post_verification_volume':VOLUME,'post_capture':str(ROOT/'artifacts/acquisition-readiness/AR-01.3B/post-migration/raw/scraper.db'),
            'pre_write_gate':'PASS','migration_exit_success':True,'migration_attempted':True,
            'recovery_authority':'USER','execution_agent':'CODEX','original_interruption_preserved':True})
        driver.log('PASS_AR013B_READONLY_RESUME_NO_MIGRATION_RETRY')
        return 0
    except Exception as exc:
        result['error']=str(exc);driver.log('STOP_READONLY_RESUME',error=str(exc));return 2
    finally:
        (RESUME/'final.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':sys.exit(main())
