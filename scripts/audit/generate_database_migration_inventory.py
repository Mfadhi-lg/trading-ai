"""Generate a static declaration inventory; never opens an application database."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'docs/database/migration-inventory.json'
DECLARATION = re.compile(r'^\s*(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|VIEW|INDEX|TRIGGER)|ALTER\s+TABLE|DROP\s+(?:TABLE|VIEW|INDEX|TRIGGER))\b', re.I)


def inventory():
    counts = dict.fromkeys(('create_table', 'create_view', 'create_index', 'create_trigger', 'alter_table', 'drop_object'), 0)
    migrations = []
    for path in sorted((ROOT / 'scrapper/db/migrations').glob('*.sql')):
        raw = path.read_bytes()
        lines = raw.decode().splitlines()
        declarations = []
        for number, line in enumerate(lines, 1):
            match = DECLARATION.match(line)
            if match:
                words = match.group(1).upper().split()
                key = 'drop_object' if words[0] == 'DROP' else ('alter_table' if words[0] == 'ALTER' else 'create_' + words[-1].lower())
                counts[key] += 1
                declarations.append({'line': number, 'text': line.strip()})
        migrations.append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest(),
                           'lines': len(lines), 'declarations': declarations})
    return {'format_version': 2, 'kind': 'current_source_inventory',
            'source_manifest_sha256': hashlib.sha256((ROOT / 'docs/cleanup/baselines/C22.3/migration-manifest.json').read_bytes()).hexdigest(),
            'source_manifest': 'docs/cleanup/baselines/C22.3/migration-manifest.json',
            'migration_count': len(migrations), 'declaration_entry_count': sum(counts.values()),
            'declaration_counts': counts, 'migrations': migrations}


if __name__ == '__main__':
    result = inventory()
    OUTPUT.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('migration_count', 'declaration_entry_count', 'declaration_counts')}))
