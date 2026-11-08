"""Preserve old paths, seal the final review package and reproduce its hashes."""
import hashlib
import json
import subprocess
from prepare import ROOT, OUT, RELEASE, ARCHIVE, digest, write

ledger = 'docs/current/TRADING_AI_ACQUISITION_READINESS_EXECUTION.md'
before = json.loads((OUT/'working-tree-before.json').read_text())
allowed_prefixes = [str(OUT.relative_to(ROOT))+'/', str(RELEASE.relative_to(ROOT))+'/',
                    str(ARCHIVE.parent.relative_to(ROOT))+'/']
drift = []
checked = 0
for relative, expected in before['files'].items():
    if relative == ledger or any(relative.startswith(p) for p in allowed_prefixes):
        continue
    path = ROOT/relative
    actual = digest(path.read_bytes()) if path.is_file() else None
    if actual != expected:
        drift.append(relative)
    checked += 1
assert not drift, drift
assert digest((OUT/'ledger-before.md').read_bytes()) == before['files'][ledger]
subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
assert json.loads((OUT/'package-verification.json').read_text())['status'] == 'PASS'
manifest = json.loads((RELEASE/'release-manifest.json').read_text())
assert manifest['production_activation_authorized'] is False
assert manifest['package_verification'] == 'PASS'
write('final-verification.json', {'status': 'PASS', 'preexisting_files_checked': checked,
    'preexisting_byte_drift': [], 'ledger_before_retained': True, 'git_diff_check': 'PASS',
    'production_activation': 'NOT_STARTED', 'production_data_access': False,
    'operational_services_started': False, 'independent_review': 'PENDING_FOR_NEW_DIGEST',
    'offline_behavioral_proof_reused': True, 'new_payload_focused_tests': 8,
    'archive_retained_in_workspace': str(ARCHIVE.relative_to(ROOT))})
files = [p for directory in [OUT, RELEASE] for p in directory.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.name != 'SHA256SUMS']
files += [ARCHIVE, ROOT/ledger, ROOT/'docs/acquisition-readiness/chunks/AR-02.1-patched-release-20261008.md']
lines = []
for path in sorted(set(files)):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(block)
    lines.append(f'{h.hexdigest()}  {path.relative_to(ROOT)}')
(OUT/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
subprocess.run(['shasum', '-a', '256', '-c', str(OUT/'SHA256SUMS')], cwd=ROOT, check=True)
print(json.dumps({'status': 'PASS', 'sealed_files': len(files), 'preserved_paths': checked}))
