"""Exactly three read-only probes at minutes 0/5/15. Never activates a role."""
import hashlib
import importlib.util
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
C = ROOT / 'docs/acquisition-readiness/baselines/AR-01.3C/attempt-02-execution'
spec = importlib.util.spec_from_file_location('readonly_host', ROOT / 'ops/docker/canary/ar013_readonly_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def utc():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if (OUT / 'attempt.json').exists() or (OUT / 'final.json').exists():
        raise RuntimeError('Existing parent observation evidence: refuse replay/overwrite')
    save(OUT / 'attempt.json', {'started_at_utc': utc(), 'scope': 'read-only minutes 0,5,15', 'production_writes': 0})
    result = {'status': 'FAIL', 'production_writes': 0, 'operational_workers_started': False,
              'network_requests': 0, 'migrations_repeated': False, 'write_canary_repeated': False,
              'offline_tests_repeated': False, 'probes': []}
    try:
        reused = []
        for rel in ['AR-01.3A/SHA256SUMS', 'AR-01.3B/SHA256SUMS', 'AR-01.3C/attempt-02-execution/SHA256SUMS']:
            index = ROOT / 'docs/acquisition-readiness/baselines' / rel
            count = 0
            for line in index.read_text().splitlines():
                expected, name = line.split(None, 1)
                assert sha(ROOT / name.strip()) == expected, 'Retained proof hash drift: ' + name
                count += 1
            reused.append({'index': str(index.relative_to(ROOT)), 'index_sha256': sha(index), 'files_verified': count})
        save(OUT / 'reused-evidence.json', reused)
        baseline = json.loads((C / 'final-readonly-gate.json').read_text())
        assert baseline['status'] == 'PASS' and baseline['control_row']['status'] == 'RELEASED'
        save(OUT / 'baseline.json', baseline)
        start = time.monotonic()
        start_epoch = time.time()
        save(OUT / 'schedule.json', {'origin_epoch': start_epoch, 'origin_utc': utc(), 'offset_seconds': [0, 300, 900]})
        for offset in (0, 300, 900):
            while time.monotonic() < start + offset:
                time.sleep(min(30, start + offset - time.monotonic()))
            folder = OUT / ('minute-%02d' % (offset // 60))
            folder.mkdir()
            image = json.loads(host.checked(['docker', 'image', 'inspect', host.IMAGE]))[0]
            assert image['Id'] == host.IMAGE.split('@')[1]
            assert image['Config']['Labels']['org.opencontainers.image.revision'] == host.SOURCE
            assert image['Config']['Labels']['org.opencontainers.image.version'] == 'c28.1-ar012-20261007'
            assert image['Os'] == 'linux' and image['Architecture'] == 'arm64'
            save(folder / 'image-identity.json', {'id': image['Id'], 'source': host.SOURCE,
                 'labels': image['Config']['Labels'], 'platform': image['Os'] + '/' + image['Architecture']})
            volume = json.loads(host.checked(['docker', 'volume', 'inspect', 'trading-ai_scraper-data']))[0]
            assert volume['Name'] == 'trading-ai_scraper-data' and volume['Labels']['com.docker.compose.project'] == 'trading-ai'
            save(folder / 'volume-identity.json', volume)
            argv = ['docker', 'run', '--rm', '--pull', 'never', '--no-healthcheck', '--network', 'none', '--read-only',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--cpus', '0.5', '--memory', '1536m',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', '--mount',
                    'type=volume,src=trading-ai_scraper-data,dst=/data,readonly', '--mount',
                    'type=bind,src=' + str(OUT) + ',dst=/proof,readonly', '--env',
                    'SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots', '--entrypoint', 'python', host.IMAGE,
                    '/proof/probe.py', folder.name]
            save(folder / 'command.json', argv)
            gate = host.writer_gate()
            gate['harness_sha256'] = sha(OUT / 'probe.py')
            save(folder / 'writer-before.json', gate)
            began = time.monotonic()
            print(json.dumps({'event': 'probe_start', 'minute': offset // 60, 'at_utc': utc(), 'elapsed_seconds': began - start}), flush=True)
            r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            duration = time.monotonic() - began
            (folder / 'stdout.log').write_text(r.stdout)
            (folder / 'stderr.log').write_text(r.stderr)
            save(folder / 'exit.json', {'exit_code': r.returncode, 'duration_seconds': duration})
            assert r.returncode == 0 and not r.stderr and duration <= 30, 'Probe failed; STOP, no retry'
            probe = json.loads(r.stdout)
            assert probe['status'] == 'PASS'
            save(folder / 'result.json', probe)
            save(folder / 'writer-after.json', host.writer_gate())
            result['probes'].append({'minute': offset // 60, 'started_offset_seconds': began - start,
                 'completed_offset_seconds': time.monotonic() - start, 'duration_seconds': duration,
                 'exit_code': r.returncode, 'status': probe['status'], 'db_sha256': probe['db_sha256']})
            assert began - start >= offset
            save(OUT / 'progress.json', result)
            print(json.dumps({'event': 'probe_PASS', **result['probes'][-1]}), flush=True)
        assert len(result['probes']) == 3 and result['probes'][-1]['started_offset_seconds'] >= 900
        result.update(status='PASS', completed_at_utc=utc(), parent_acceptance='PASS_FOR_BOUNDED_CANARY_SCOPE')
    except Exception as exc:
        result['error'] = str(exc)
        print(json.dumps({'event': 'STOP', 'error': str(exc)}), flush=True)
    finally:
        save(OUT / 'final.json', result)
    return 0 if result['status'] == 'PASS' else 2

if __name__ == '__main__':
    raise SystemExit(main())
