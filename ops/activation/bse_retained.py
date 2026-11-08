"""Prepare/execute one authorized BSE retained-input operation; never retry."""
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/bse-production-ingestion-wal-validated-2026-10-09'
BASE='trading-ai-ar02-period-release@sha256:a7025c3e70ea14939874aa2c1337a41845e3311ba9b5a4fc1a1004c6d6f4c16d'
TAG='trading-ai-bse-retained:20261009'
VOLUME='trading-ai_scraper-data'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(args):
    return subprocess.check_output(args,text=True,timeout=60).strip()


def require(condition,message):
    if not condition:
        raise RuntimeError(message)


def main():
    from production_ownership import owner_gate
    require('--execute' in sys.argv, 'This selected operation requires --execute')
    OUT.mkdir(exist_ok=True)
    require(not (OUT/'execution-started.json').exists(), 'An execution was already started; do not automatically repeat it')
    spec=importlib.util.spec_from_file_location('nse_baseline_activation',HERE/'nse_results.py')
    base=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    release=base.check_configuration()  # Existing sealed release/source checks.
    source=json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-02-period-20261008/source-manifest.json').read_text())
    files={f['path']:f['sha256'] for f in source['files']}
    application_paths=('scrapper/config.py','scrapper/runtime_identity.py','scrapper/core/http.py',
        'scrapper/db/database.py','scrapper/db/exchange/filings.py','scrapper/db/exchange/snapshots.py',
        'scrapper/db/repo.py','scrapper/db/screener_discovery_repo.py',
        'scrapper/sources/exchange/result_filings.py','scrapper/sources/exchange/tabular.py',
        'scrapper/sources/exchange/snapshot_guard.py','scrapper/sources/exchange/transport.py',
        'scrapper/workflows/exchange/universe.py','scrapper/operations/coordinator/exchange.py')
    require(all(digest(ROOT/p)==files[p] for p in application_paths), 'Canonical source differs from sealed image')
    # Imported OCI images can lose a resolvable repo@digest alias when retagged.
    # Resolve the sealed content ID itself, then perform the same identity gate.
    image=json.loads(checked(['docker','image','inspect',release['oci_index_digest']]))[0]
    require(image['Id']==release['oci_index_digest'] and image['Os']=='linux' and image['Architecture']=='arm64',
            'Pinned parent image identity mismatch')
    volume=json.loads(checked(['docker','volume','inspect',VOLUME]))[0]
    require(volume['Name']==VOLUME and volume['Labels'].get('com.docker.compose.project')=='trading-ai',
            'Production volume identity mismatch')
    permission={'collection':True,'electronic_storage':True,'project_reuse':True,
        'selected_operation':'eight retained October 9 BSE filings only',
        'evidence':'User confirms existing hard-copy BSE written consent covers automated collection, electronic storage and reuse for this project; paper cannot be photographed/scanned',
        'confirmation':'yes it confirms that; yes i have the written file but it is security paper; countinue',
        'continuous_collection_authorized_by_this_operation':False,
        'document_independently_inspected':False}
    (OUT/'permission.json').write_text(json.dumps(permission,indent=2)+'\n')
    evidence=ROOT/'artifacts/bse-visible-runner-verification-2026-10-09'
    inputs=('receipts/2026-10-09-morning.json','receipts/2026-10-09-morning.raw','verification.json')
    app_hashes={p:files[p] for p in application_paths}
    addon='scrapper/sources/exchange/browser_results.py'
    app_hashes[addon]=digest(ROOT/addon)
    require('64 passed' in (ROOT/'artifacts/bse-browser-integration-2026-10-09/focused-tests.log').read_text(),
            'Focused integration test evidence missing')
    # Reuse the prepared immutable application image. Operator scripts are
    # hash-verified read-only inputs, as in the existing NSE one-shot runner.
    built=json.loads(checked(['docker','image','inspect',TAG]))[0]
    prepared=json.loads((ROOT/'artifacts/bse-production-ingestion-ownership-fixed-2026-10-09/manifest.json').read_text())
    require(built['Id']==prepared['operation_image'] and prepared['parent_image']==BASE,
            'Prepared canonical BSE image differs from the retained package')
    operator=OUT/'operator'
    operator.mkdir(exist_ok=True)
    for name in ('bse_retained_runtime.py','production_ownership.py','sqlite_sidecar_guard.py'):
        (operator/name).write_bytes((HERE/name).read_bytes())
    identity=json.loads((HERE/'runtime-identity-nse-results.json').read_text())
    identity.update(authorized=True,release_id='bse-retained-20261009',image_id=built['Id'])
    (OUT/'runtime-identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    for name,path,role in (('store-sentinel.json','/data','store'),('snapshots-sentinel.json','/data/snapshots','documents')):
        (OUT/name).write_text(json.dumps({'environment':'production','path':path,
            'release_id':identity['release_id'],'roles':[role]},indent=2)+'\n')
    production=json.loads((ROOT/'artifacts/bse-browser-integration-2026-10-09/production-projection.json').read_text())
    manifest={'parent_image':BASE,'operation_image':built['Id'],
        'application_hashes':app_hashes,'input_hashes':{p:digest(evidence/p) for p in inputs},
        'runtime_sha256':digest(HERE/'bse_retained_runtime.py'), 'expected_holds':production['holds'],
        'production_ownership_sha256':digest(HERE/'production_ownership.py'),
        'sqlite_sidecar_guard_sha256':digest(HERE/'sqlite_sidecar_guard.py'),
        'test_log_sha256':digest(ROOT/'artifacts/bse-browser-integration-2026-10-09/focused-tests.log'),
        'permission_sha256':digest(OUT/'permission.json'), 'automatic_retry':False,
        'packaging_reused':True,'operator_delivery':'hash-verified read-only bind; canonical application image unchanged'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for name,original in (('production-baseline.json',HERE/'production-baseline.json'),
        ('selected-filings.json',evidence/'selected-filings.json'),
        ('expected-changes.json',ROOT/'artifacts/bse-browser-integration-2026-10-09/expected-changes.json')):
        (OUT/name).write_bytes(original.read_bytes())

    with (HERE/'.nse-owner.lock').open('a') as lock:  # Shared host operation owner.
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        owner=owner_gate()  # Corrected evidence-based host/container/scheduled gate.
        started={'owner_gate':owner,'started_at_utc':datetime.now(timezone.utc).isoformat(),
                 'image':built['Id'],'manifest_sha256':digest(OUT/'manifest.json'),
                 'permission_scope':'collection + electronic storage + project reuse attested',
                 'max_filing_rows':8,'max_new_queue_items':3,'network':'none','restart_other_services':False}
        (OUT/'execution-started.json').write_text(json.dumps(started,indent=2)+'\n')
        command=['docker','run','--rm','--name','trading-ai-bse-retained-once','--network','none',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--cpus','0.5','--memory','1536m','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
            '--mount','type=volume,source='+VOLUME+',target=/data',
            '--mount','type=volume,source='+VOLUME+',target=/physical,readonly',
            '--mount','type=bind,source='+str(evidence)+',target=/input,readonly',
            '--mount','type=bind,source='+str(OUT)+',target=/checks,readonly',
            '--mount','type=bind,source='+str(OUT/'runtime-identity.json')+',target=/run/trading-ai/runtime-identity.json,readonly',
            '--mount','type=bind,source='+str(OUT/'store-sentinel.json')+',target=/data/.trading_ai_runtime_identity.json,readonly',
            '--mount','type=bind,source='+str(OUT/'snapshots-sentinel.json')+',target=/data/snapshots/.trading_ai_runtime_identity.json,readonly',
            '--env','PYTHONDONTWRITEBYTECODE=1','--env','TRADING_AI_ENV=production',
            '--env','TRADING_AI_RUNTIME_IDENTITY_FILE=/run/trading-ai/runtime-identity.json',
            '--env','SCRAPER_DB_PATH=/data/scraper.db','--env','SCRAPER_STORE_PATH=/data',
            '--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots','--entrypoint','python',built['Id'],
            '/checks/operator/bse_retained_runtime.py']
        (OUT/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        with (OUT/'runtime.log').open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=360)
        outcome={'status':'PASS' if result.returncode==0 else 'STOP','exit_code':result.returncode,
                 'automatic_retry':False,'owner_after':owner_gate(),
                 'finished_at_utc':datetime.now(timezone.utc).isoformat()}
        (OUT/'outcome.json').write_text(json.dumps(outcome,indent=2)+'\n')
        print(json.dumps(outcome))
        return result.returncode


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        OUT.mkdir(exist_ok=True)
        (OUT/'stop.json').write_text(json.dumps({'status':'STOP','error':str(exc),
            'automatic_retry':False,'stopped_at_utc':datetime.now(timezone.utc).isoformat()},indent=2)+'\n')
        print('STOP: '+str(exc),file=sys.stderr)
        sys.exit(2)
