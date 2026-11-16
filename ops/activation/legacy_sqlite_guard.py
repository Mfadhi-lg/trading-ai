"""Fail-closed boundary for retained SQLite campaign tools (issue #100)."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ALTERNATIVES = (
    'Use ./scraper status/doctor for current observations, '
    'postgresql_runtime.preflight for native admission, and '
    'ops/host/postgresql_backup.py for native backups. '
    'See docs/operations/legacy-sqlite-launchers.md.'
)


def reject_native(root=None):
    """Check environment AND selected release; never fall back on missing targets."""
    if os.environ.get('TRADING_AI_DB_BACKEND', 'sqlite') != 'sqlite' or os.environ.get('DATABASE_URL'):
        raise RuntimeError('Legacy SQLite operation refused for native configuration. ' + ALTERNATIVES)
    from acquisition_release import active_root
    root = Path(root) if root is not None else (Path('/app') if Path(__file__).parent == Path('/activation') else Path(__file__).resolve().parents[2])
    for selected in {root, active_root(root)}:
        activation = selected / 'ops/activation'
        # A malformed or missing declared target must also fail closed.
        manifest = activation / 'automatic-acquisition-manifest.json'
        declared = manifest.exists() and 'ops/activation/postgresql-target.json' in json.loads(manifest.read_text()).get('files', {})
        if (activation / 'postgresql-target.json').exists() or declared:
            raise RuntimeError('Legacy SQLite operation refused for native release. ' + ALTERNATIVES)
    container_manifest = Path('/activation/automatic-acquisition-manifest.json')
    container_declared = container_manifest.exists() and 'ops/activation/postgresql-target.json' in json.loads(container_manifest.read_text()).get('files', {})
    if Path('/activation/postgresql-target.json').exists() or container_declared:
        raise RuntimeError('Legacy SQLite operation refused for native container release. ' + ALTERNATIVES)


def retired_launcher():
    """Old host wrappers bind production volumes; they are never replay launchers."""
    raise RuntimeError('Historical snapshot-only launcher retired; production volume access refused. ' + ALTERNATIVES)


def retired_bse_ingestion():
    """The dated eight-filing write operation has no supported replay/native mode."""
    raise RuntimeError('Retained BSE ingestion retired; production volume access refused. '
                       'The October authorization is historical, not permission to replay. '
                       + ALTERNATIVES)


def require_snapshot():
    """Only an explicitly named/hash-pinned, read-only historical container input."""
    reject_native()
    source = os.environ.get('TRADING_AI_HISTORICAL_SQLITE_INPUT')
    digest = os.environ.get('TRADING_AI_HISTORICAL_SQLITE_SHA256', '')
    if (os.environ.get('TRADING_AI_ENV') != 'test' or source != '/data/scraper.db'
            or not re.fullmatch('[0-9a-f]{64}', digest)):
        raise RuntimeError('Historical snapshot-only: explicit test input and SHA256 required. ' + ALTERNATIVES)
    rows = [line.split(' - ', 1)[0].split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    matches = [row for row in rows if len(row) > 5 and (source == row[4] or source.startswith(row[4].rstrip('/') + '/'))]
    effective = max(matches, key=lambda row: len(row[4]), default=None)
    if effective is None or effective[4] not in ('/data', source) or 'ro' not in effective[5].split(','):
        raise RuntimeError('Historical snapshot must be mounted read-only at /data or /data/scraper.db')
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Historical snapshot must be an existing regular file')
    actual = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            actual.update(block)
    if actual.hexdigest() != digest:
        raise RuntimeError('Historical snapshot SHA256 mismatch')
    print('HISTORICAL_SNAPSHOT_ONLY sha256=' + digest + '; not current runtime or native backup evidence', file=sys.stderr)
