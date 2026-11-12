"""Load BSE runtime identity from sealed production configuration, not receipts."""
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def production_config():
    manifest = json.loads((HERE / 'automatic-acquisition-manifest.json').read_text())
    if ('ops/activation/postgresql-target.json' in manifest['files']
            and not (HERE / 'postgresql-target.json').is_file()):
        raise RuntimeError('Declared PostgreSQL production target is missing')
    # Native releases already carry their image and application inventory here.
    if (HERE / 'postgresql-target.json').exists():
        from postgresql_release import verify
        native = verify()
        return {'operation_image': native['image'],
                'application_hashes': native['application_hashes']}
    path = HERE / 'bse-production.json'
    if manifest['files'].get('ops/activation/bse-production.json') != hashlib.sha256(path.read_bytes()).hexdigest():
        raise RuntimeError('BSE production configuration is not manifest-bound')
    value = json.loads(path.read_text())
    hashes = value.get('application_hashes')
    if (not re.fullmatch(r'sha256:[0-9a-f]{64}', value.get('operation_image', ''))
            or not isinstance(hashes, dict) or not hashes):
        raise RuntimeError('Invalid BSE production configuration')
    for name, digest in hashes.items():
        parts = Path(name).parts
        if (not parts or parts[0] != 'scrapper' or '..' in parts
                or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise RuntimeError('Invalid BSE application inventory')
    return value
