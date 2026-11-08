"""Use existing production ownership and immutable inspection protections."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT/'ops/host'),str(ROOT)]


def main():
    from acquisition_lifecycle import lock, VOLUME
    from production_ownership import owner_gate
    from document_backlog_recovery import window_safe
    from phase4_documents import IMAGE, STORE, storage_preflight
    out=ROOT/'artifacts/acquisition-actual-download-recovery'
    with lock(ROOT/'ops/activation/.scheduler-admission.lock'):
        with lock(ROOT/'ops/activation/.nse-owner.lock'):
            window_safe(50)
            owner=owner_gate()
            volume=json.loads(subprocess.check_output(['docker','volume','inspect',VOLUME]))[0]
            if volume['Labels']['com.docker.compose.project']!='trading-ai':raise RuntimeError('Volume identity differs')
            try:storage=storage_preflight()
            except RuntimeError as exc:storage={'status':'BLOCKED','reason':str(exc)}
            (out/'raw/preflight.json').write_text(json.dumps({'owner':owner,'volume':volume,'storage':storage},indent=2)+'\n')
            result=subprocess.run(['docker','run','--rm','--pull=never','--network=none','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=1536m',
                '--mount',f'type=volume,source={VOLUME},target=/data,readonly',
                '--mount',f'type=bind,source={ROOT / "ops/activation"},target=/activation,readonly',
                '--mount',f'type=bind,source={ROOT / "ops/host"},target=/host,readonly',
                '--mount',f'type=bind,source={ROOT / "scrapper"},target=/app/scrapper,readonly',
                *(['--mount',f'type=bind,source={STORE},target=/documents,readonly'] if storage.get('status')!='BLOCKED' else []),
                '--mount',f'type=bind,source={out},target=/evidence',
                '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,
                '/host/actual_download_audit.py'],capture_output=True,text=True,timeout=90)
            (out/'raw/audit-process.json').write_text(json.dumps({'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr},indent=2)+'\n')
            print(result.stdout or result.stderr)
            result.check_returncode()


if __name__=='__main__':main()
