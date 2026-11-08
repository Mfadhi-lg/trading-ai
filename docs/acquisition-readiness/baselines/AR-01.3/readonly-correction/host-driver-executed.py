"""Host ownership gate and isolated read-only probe. No activation operations."""
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/acquisition-readiness/baselines/AR-01.3/readonly-correction'
IMAGE = 'trading-ai-c281-release@sha256:4d289faa67334bc870d4f2a6a181feecee777fd427eac209b9adef68ecab0238'
SOURCE = 'c3ddbbb9c298d07b8b46a362996251417282476a'
PROBE = Path(__file__).with_name('ar013_readonly_db.py')


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def checked(args):
    r = run(args)
    if r.returncode:
        raise RuntimeError('Read-only inventory command failed: ' + shlex.join(args) + '\n' + r.stderr)
    return r.stdout


def writer_gate():
    ids = checked(['docker', 'ps', '-aq']).split()
    rows = json.loads(checked(['docker', 'inspect', *ids])) if ids else []
    containers = [{'id': r['Id'], 'name': r['Name'], 'running': r['State']['Running'],
                   'status': r['State']['Status'], 'image': r['Image'],
                   'project': r['Config'].get('Labels', {}).get('com.docker.compose.project'),
                   'mounts': [{k: m.get(k) for k in ('Type', 'Name', 'Source', 'Destination', 'RW')} for m in r['Mounts']]}
                  for r in rows]
    # Deliberately stricter than only known Trading AI names: no other active
    # container may own the production volume under an unfamiliar name.
    if any(r['running'] or r['status'] not in ('exited', 'created', 'dead') for r in containers):
        raise RuntimeError('A container is active/restarting/paused; writer ownership not proven')
    relevant, unknown = [], []
    # macOS truncates an intermediate comm column to 15 characters. Read it
    # as the final column separately rather than guessing a binary from args.
    binaries = {line.strip().split(None, 1)[0]: line.strip().split(None, 1)[1]
                for line in checked(['ps', '-axo', 'pid=,comm=']).splitlines()
                if len(line.strip().split(None, 1)) == 2}
    processes = checked(['ps', '-ww', '-axo', 'pid=,args='])
    for line in processes.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        pid, command = parts
        executable = binaries.get(pid, 'unknown')
        if not re.search(r'trading[-_]ai|scrapper|screener|scraper\.db|artifact[-_]reconciler', command, re.I):
            continue
        if int(pid) == os.getpid():
            classification = 'this_readonly_host_gate'
        elif (Path(executable).name in ('zsh', 'bash', 'sh')
              and command.endswith('python3 ' + str(Path(__file__).resolve()))):
            classification = 'readonly_gate_launcher'
        elif Path(executable).name in ('agy', 'codex'):
            classification = 'development_editor_or_agent_not_db_worker'
        elif 'scrapper.commands.publish_document_store_capacity' in command:
            classification = 'document_capacity_publisher_not_db_writer'
        else:
            classification = 'unknown_possible_db_writer'
            unknown.append(int(pid))
        # Do not retain arbitrary process arguments, which may contain tokens.
        relevant.append({'pid': int(pid), 'executable': executable, 'classification': classification})
    if unknown:
        raise RuntimeError('Unknown host process may own DB: ' + str(unknown))
    cron = run(['crontab', '-l'])
    cron_absent = cron.returncode == 1 and 'no crontab for' in cron.stderr
    cron_empty = cron.returncode == 0 and not any(l.strip() and not l.lstrip().startswith('#') for l in cron.stdout.splitlines())
    if not (cron_absent or cron_empty):
        raise RuntimeError('User cron is unreadable or has unreviewed tasks')
    launch_inventory = []
    folders = [Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons'), Path.home() / 'Library/LaunchAgents']
    for folder in folders:
        if not folder.is_dir():
            raise RuntimeError('Launch inventory directory missing: ' + str(folder))
        for path in sorted(folder.glob('*.plist')):
            # Two unrelated vendor launch plists prefix their XML declaration
            # with whitespace. Normalize that prefix only; unparseable files
            # still stop the ownership inventory instead of being skipped.
            try:
                payload = plistlib.loads(path.read_bytes().lstrip())
            except Exception as exc:
                raise RuntimeError('Unparseable launch inventory: ' + str(path)) from exc
            text = json.dumps(payload)
            if not re.search(r'trading[-_]ai|scrapper|screener|scraper\.db', text, re.I):
                continue
            args = payload.get('ProgramArguments', [])
            capacity_only = (payload.get('Label') == 'com.trading-ai.document-capacity'
                             and args[1:] == ['-m', 'scrapper.commands.publish_document_store_capacity',
                                              '--root', '/Volumes/My Passport/passport/trading-ai', '--loop', '300']
                             and not payload.get('EnvironmentVariables'))
            launch_inventory.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                     'label': payload.get('Label'), 'classification': 'document_capacity_only' if capacity_only else 'unknown'})
            if not capacity_only:
                raise RuntimeError('Unreviewed scheduled Trading AI task: ' + str(path))
    loaded = checked(['launchctl', 'list'])
    relevant_loaded = [l for l in loaded.splitlines() if re.search(r'trading[-_]ai|scrapper|screener', l, re.I)]
    if any(l.split()[-1] != 'com.trading-ai.document-capacity' for l in relevant_loaded):
        raise RuntimeError('Unknown loaded Trading AI launch task')
    return {'writer_gate_passed': True, 'observed_at_epoch': time.time(),
            'containers': containers, 'host_processes': relevant,
            'user_cron': 'none', 'scheduled_tasks': launch_inventory,
            'loaded_relevant_launch_tasks': relevant_loaded,
            'known_external_db_writers': [],
            'scope': 'all Docker containers; host process inventory; user cron; host launch plists/user launch labels'}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {'CANARY_HARNESS': 'FAIL', 'docker_launched': False}
    try:
        if (OUT / 'result.json').exists():
            raise RuntimeError('Evidence already exists; refuse to overwrite or repeat probe')
        image = json.loads(checked(['docker', 'image', 'inspect', IMAGE]))[0]
        if image['Id'] != IMAGE.split('@', 1)[1] or image['Config']['Labels'].get('org.opencontainers.image.revision') != SOURCE:
            raise RuntimeError('Loaded immutable image/source mismatch')
        volume = json.loads(checked(['docker', 'volume', 'inspect', 'trading-ai_scraper-data']))[0]
        if volume['Name'] != 'trading-ai_scraper-data' or volume['Labels'].get('com.docker.compose.project') != 'trading-ai':
            raise RuntimeError('Existing production volume identity mismatch')
        summary['image_identity'] = {'id': image['Id'], 'source': SOURCE,
                                     'platform': image['Os'] + '/' + image['Architecture']}
        summary['volume_identity'] = volume
        shutil.copyfile(PROBE, OUT / PROBE.name)
        args = ['docker', 'run', '--rm', '--pull', 'never', '--no-healthcheck',
                '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--cpus', '0.5', '--memory', '1536m',
                '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m',
                '--mount', 'type=volume,src=trading-ai_scraper-data,dst=/data,readonly',
                '--mount', 'type=bind,src=' + str(OUT) + ',dst=/proof,readonly',
                '--entrypoint', 'python', IMAGE, '/proof/' + PROBE.name]
        (OUT / 'docker-command.txt').write_text(shlex.join(args) + '\n')
        # Inventory is taken last, immediately before invoking the container.
        gate = writer_gate()
        gate.update({'source_commit': SOURCE, 'harness_sha256': hashlib.sha256(PROBE.read_bytes()).hexdigest()})
        (OUT / 'host-gate.json').write_text(json.dumps(gate, indent=2) + '\n')
        summary['docker_launched'] = True
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        (OUT / 'stdout.log').write_text(r.stdout)
        (OUT / 'stderr.log').write_text(r.stderr)
        summary['docker_exit_code'] = r.returncode
        summary['probe'] = json.loads(r.stdout)
        after = writer_gate()
        (OUT / 'host-gate-after.json').write_text(json.dumps(after, indent=2) + '\n')
        if r.returncode != 0 or r.stderr or summary['probe']['CANARY_HARNESS'] != 'PASS':
            raise RuntimeError('Container returned a failure or unexpected stderr')
        summary['CANARY_HARNESS'] = 'PASS'
        summary['exit_classification'] = 'PASS_READ_ONLY_ACCESS_WITH_EXPECTED_SCHEMA_BLOCK'
        return 0
    except Exception as exc:
        summary['error'] = str(exc)
        summary['exit_classification'] = 'FAIL_CLOSED'
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        (OUT / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    sys.exit(main())
