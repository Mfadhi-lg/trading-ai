"""Manual canonical CLI on the manifest-bound native release; no SQLite fallback."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT)]


def main(arguments=None):
    from acquisition_release import workspace_root
    from postgresql_release import verify,configure
    from phase4_documents import STORE
    from production_ownership import owner_gate
    verify()
    workspace=workspace_root(ROOT)
    out=workspace/'artifacts/postgresql-manual-cli'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,mode=0o700)
    activation=ROOT/'ops/activation'
    compose=json.loads((activation/'compose.nse.json').read_text())
    service=compose['services'].pop('nse-results');compose['services']={'cli':service}
    compose['name']='trading-ai-native-manual'
    service.update(profiles=['manual'],entrypoint=['python','-m','scrapper.cli'],command=[],restart='no')
    for mount in service['volumes']:
        if mount['type']=='bind':mount['source']=str(activation)
    service['volumes'] += [
        {'type':'bind','source':str(STORE),'target':'/documents','bind':{'create_host_path':False}},
        {'type':'bind','source':str(workspace/'.screener_session'),'target':'/run/secrets/screener_session',
         'read_only':True,'bind':{'create_host_path':False}}]
    service['environment'].update(SCRAPER_DOCUMENT_STORE_PATH='/documents',DOCUMENT_REQUIRE_SENTINEL='1',
        SCREENER_SESSION_FILE='/run/secrets/screener_session',PYTHONDONTWRITEBYTECODE='1')
    identity=json.loads((activation/'runtime-identity-nse-results.json').read_text())
    identity['targets']['documents']='/documents'
    identity.setdefault('target_release_ids',{})['documents']='c05.3-release-20261003'
    identity_path=out/'identity.json';identity_path.write_text(json.dumps(identity,indent=2)+'\n')
    compose['configs']['nse_identity']['file']=str(identity_path)
    for key in ('nse_store','nse_snapshots'):
        compose['configs'][key]['file']=str((activation/compose['configs'][key]['file']).resolve())
    configure(compose,service,'financial',identity_path)
    path=out/'compose.json';path.write_text(json.dumps(compose,indent=2)+'\n')
    with (activation/'.scheduler-admission.lock').open('a') as admission:
        fcntl.flock(admission,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (activation/'.nse-owner.lock').open('a') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            owner_gate()
            return subprocess.run(['docker','compose','-f',str(path),'--profile','manual',
                'run','--rm','--no-deps','cli',*(sys.argv[1:] if arguments is None else arguments)]).returncode

if __name__=='__main__':raise SystemExit(main())
