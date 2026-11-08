"""Local-only source export. Freeze exact paths first; copy only that manifest.

No application imports, production DB reads, credential copies or remote calls.
Run once; refuses to overwrite an existing candidate.
"""
from pathlib import Path
import hashlib
import json
import os
import stat
import subprocess
import tarfile

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
DEST = ROOT / 'github-source-candidate-20261023'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def main():
    if DEST.exists():
        raise SystemExit('Candidate already exists; refusing overwrite')
    selected = set()
    for folder in ('scrapper', 'ops/activation', 'ops/host'):
        for p in (ROOT / folder).rglob('*'):
            rel = p.relative_to(ROOT)
            if 'store' in rel.parts or '__pycache__' in rel.parts or '.venv' in rel.parts:
                continue
            if p.suffix in {'.py', '.sh'} and '.phase4-candidate.' not in p.name:
                selected.add(str(rel))
    for pattern in ('scrapper/db/migrations/*.sql', 'scrapper/requirements*',
                    'scrapper/sources/manifests/*.json', 'scrapper/sources/manifests/*.yml'):
        selected.update(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern) if p.is_file())
    selected.update((
        'Dockerfile', '.dockerignore', 'scraper', 'scraper-monitor', 'screener-token',
        'ops/host/browser-requirements.lock',
        'scrapper/documents/rating_company_aliases.json',
        'scrapper/documents/download/certificates/rapidssl-tls-rsa-ca-g1.pem',
        'scrapper/research/amfi_amc_source_inventory.json',
        'scripts/audit/check_migration_manifest.py',
        'scripts/audit/test_migration_lineage.py',
        'scrapper/README.md', 'docs/architecture/FUTURE_DOMAIN_CONTRACT.md',
        'docs/cleanup/baselines/C22.3/migration-manifest.json',
    ))
    rows = []
    for name in sorted(selected):
        p = ROOT / name
        if not p.is_file() or p.is_symlink():
            raise RuntimeError('Nonregular/missing inclusion: ' + name)
        data = p.read_bytes()
        if b'\0' in data or len(data) > 2*1024*1024:
            raise RuntimeError('Binary/oversize inclusion requires review: ' + name)
        rows.append(dict(source=name, destination=name, sha256=sha(data), bytes=len(data),
                         mode=oct(stat.S_IMODE(p.stat().st_mode)), operation='copy'))
    # This is the explicit copy authority, not the Git index or a recursive copy.
    dump(OUT / 'inclusion-list.json', rows)
    DEST.mkdir()
    for row in json.loads((OUT / 'inclusion-list.json').read_text()):
        data = (ROOT / row['source']).read_bytes()
        if sha(data) != row['sha256']:
            raise RuntimeError('Concurrent source drift: ' + row['source'])
        target = DEST / row['destination']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if int(row['mode'], 8) & 0o111 else 0o644)
    # Extract one reviewed SQL member as plain source, never the archive itself.
    manifest_path = DEST / 'docs/cleanup/baselines/C22.3/migration-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for item in manifest['historical_identities']:
        with tarfile.open(ROOT / item['archive'], 'r:gz') as archive:
            entries = [m for m in archive.getmembers() if m.name == item['member']]
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 100000:
                raise RuntimeError('Unsafe historical SQL member')
            data = archive.extractfile(entries[0]).read()
        if sha(data) != item['sha256'] or b'\0' in data:
            raise RuntimeError('Historical migration identity mismatch')
        name = 'docs/migration-history/' + item['filename']
        p = DEST / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        rows.append(dict(source=item['archive'] + '::' + item['member'], destination=name,
                         sha256=sha(data), bytes=len(data), operation='extract_exact_sql_member'))
        item['source_file'] = name
    dump(manifest_path, manifest)
    checker = DEST / 'scripts/audit/check_migration_manifest.py'
    text = checker.read_text()
    text = text.replace('    archive_path = ROOT / item["archive"]',
        '    # Export-only plain SQL identity; legacy archive fixture support retained.\n'
        '    if "source_file" in item:\n'
        '        return sha256_file(ROOT / item["source_file"])\n'
        '    archive_path = ROOT / item["archive"]')
    checker.write_text(text)
    dump(OUT / 'original-to-export.json', rows)
    dump(OUT / 'candidate-location.json', {'path':str(DEST), 'fresh_history_pending': True})
    print('Candidate copied:', len(rows), 'source/control paths')

if __name__ == '__main__':
    main()
