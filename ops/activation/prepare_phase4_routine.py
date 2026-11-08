"""Prepare exact rollout; --activate only after approval of the retained scope.

Uses the installed dispatcher/manifest/ownership; no launchd job or migration.
"""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import argparse,fcntl,hashlib,json,shutil
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OUT=ROOT/'artifacts/phase4/routine/rollout'


def sources():
    files=list((ROOT/'scrapper').rglob('*.py'))+list((ROOT/'scrapper/documents/download/certificates').glob('*.pem'))
    files += [HERE/n for n in ('phase4_schedule.py','phase4_schedule.json','phase4_routine_runtime.py',
        'automatic_acquisition.phase4-candidate.py','prepare_phase4_routine.py','monitor_phase4_receipts.py','verify_phase4_unattended.py')]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if '.venv' not in p.parts}


def main():
    from phase4_schedule import settings,prepare
    from phase4_documents import storage_preflight
    from automatic_acquisition import verify_config
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--activate',action='store_true');p.add_argument('--prepare',action='store_true');a=p.parse_args()
    if not (a.prepare or a.activate):p.error('Select --prepare or the authorized --activate')
    if a.prepare:
        OUT.mkdir(parents=True,exist_ok=True)
        baseline=verify_config()
        for stage in ('DOCUMENT_DISCOVERY','DOCUMENT_DOWNLOAD','DOCUMENT_PROCESSING','DOCUMENT_PATROL','DOCUMENT_RATING'):
            prepare(OUT/stage.lower(),stage,authorized=False)
        seal={'sources':sources(),'installed_dispatcher_manifest_sha256':hashlib.sha256((HERE/'automatic-acquisition-manifest.json').read_bytes()).hexdigest(),
            'scope':settings(),'prepared_at_utc':datetime.now(timezone.utc).isoformat(),
            'state':'PREPARED; activation requires user approval'}
        (OUT/'seal.json').write_text(json.dumps(seal,indent=2)+'\n')
        print(json.dumps({'status':'PREPARED','scope':settings(),'installed_files_changed':False}));return
    seal=json.loads((OUT/'seal.json').read_text())
    if sources()!=seal['sources']:raise RuntimeError('Prepared source changed; regenerate and report scope')
    if hashlib.sha256((HERE/'automatic-acquisition-manifest.json').read_bytes()).hexdigest()!=seal['installed_dispatcher_manifest_sha256']:raise RuntimeError('Installed scheduler changed since preparation')
    if (OUT/'activation.json').exists():raise RuntimeError('Initial rollout already activated; preserve receipt')
    # Both are existing locks; activation cannot race a admitted production slot.
    with (HERE/'.scheduler-admission.lock').open('a') as admission,(HERE/'.nse-owner.lock').open('a') as owner:
        fcntl.flock(admission,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from production_ownership import owner_gate
        before=owner_gate();capacity=storage_preflight();baseline=verify_config()
        for name in ('automatic_acquisition.py','automatic-acquisition-manifest.json','phase4_schedule.json'):
            shutil.copyfile(HERE/name,OUT/('before-'+name))
        config=settings();now=datetime.now(timezone.utc)
        config.update(enabled=True,activated_at_utc=now.isoformat(),expires_at_utc=(now+timedelta(hours=24)).isoformat(),
            initial_free_bytes=capacity['free_bytes'],authorization='User-approved initial 24-hour routine scope')
        # Record intent before mutation. Existing admissions are crash durable.
        receipt={'activated_at_utc':now.isoformat(),'scope':config,'owner_before':before,'capacity':capacity}
        (OUT/'activation.json').write_text(json.dumps(receipt,indent=2)+'\n')
        (HERE/'phase4_schedule.json').write_text(json.dumps(config,indent=2)+'\n')
        shutil.copyfile(HERE/'automatic_acquisition.phase4-candidate.py',HERE/'automatic_acquisition.py')
        # Reuse the installed manifest, retaining its Phase3 entries and evidence.
        for name in set(baseline['files'])|set(seal['sources']):
            baseline['files'][name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        (HERE/'automatic-acquisition-manifest.json').write_text(json.dumps(baseline,indent=2)+'\n')
        verify_config()
        (OUT/'activation-verified.json').write_text(json.dumps({'scope':settings(),'owner_after':owner_gate()},indent=2)+'\n')
    print(json.dumps({'status':'ACTIVATED','expires_at_utc':config['expires_at_utc'],'historical_limit':0,'new_launchd_jobs':0}))

if __name__=='__main__':main()
