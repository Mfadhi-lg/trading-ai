"""Fail-closed Passport discovery and immutable, indexed storage."""
from __future__ import annotations
import gzip
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/historical-candle-archive-v1'
UUID = '60E1204B-CB98-48C4-9398-1D2FBAC9B44A'
NORMALIZATION_VERSION='hcd01-v4'

def dataset_identity(request_id,digest,definition,status,quality,context_revision=''):
    return sha((NORMALIZATION_VERSION+request_id+digest+definition+status+quality+context_revision).encode())

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True, default=str) + '\n').encode()
    atomic(path, raw)

def atomic(path, raw):
    path = Path(path)
    tmp = path.with_name(path.name + '.pending')
    with tmp.open('wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_directory(path.parent)

def fsync_directory(path):
    descriptor=os.open(path,os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def discover():
    matches = []
    for p in Path('/Volumes').iterdir():
        if p.is_symlink() or not p.is_mount():
            continue
        try:
            result = subprocess.run(['diskutil', 'info', '-plist', str(p)], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode:
            continue
        info = plistlib.loads(result.stdout)
        if info.get('VolumeUUID') == UUID:
            matches.append((p, info))
    if len(matches) != 1:
        raise RuntimeError('STOP_PASSPORT_MISSING_OR_AMBIGUOUS; no fallback')
    p, info = matches[0]
    if info.get('Internal') is not False or not info.get('WritableVolume'):
        raise RuntimeError('STOP_EXTERNAL_WRITABLE_VOLUME_REQUIRED')
    return p, info

def verify(mount, device):
    mount = Path(mount)
    if (not mount.is_mount() or mount.is_symlink() or mount.stat().st_dev != device
            or mount.stat().st_dev == Path('/').stat().st_dev):
        raise RuntimeError('STOP_PASSPORT_DETACHED_OR_REPLACED; no writes')
    if shutil.disk_usage(mount).free < 100 * 1024**3:
        raise RuntimeError('STOP_100GIB_RESERVE')

def audit():
    mount, info = discover()
    device = mount.stat().st_dev
    verify(mount, device)
    errors, files, duplicates = [], [], {}
    # Inventory market-data candidates throughout Passport; never hash the PDF store.
    for base, dirs, names in os.walk(mount, onerror=lambda e: errors.append(str(e.filename))):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in names:
            path = Path(base) / name
            if path.suffix.lower() in ('.parquet', '.arrow', '.feather', '.h5', '.hdf5') or (
                    path.suffix.lower() in ('.csv', '.json', '.gz', '.sqlite', '.db')
                    and any(k in str(path).lower() for k in ('candle', 'ohlcv', 'upstox', 'market-data'))):
                stat = path.stat()
                record = dict(path=str(path), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
                if stat.st_size < 64*1024**2:
                    record['sha256'] = sha(path.read_bytes())
                    duplicates.setdefault(record['sha256'], []).append(str(path))
                files.append(record)
    existing = mount / 'passport/trading-ai'
    if not (existing / '.trading_ai_document_store').is_file():
        raise RuntimeError('STOP_EXISTING_TRADING_AI_STORE_NOT_FOUND')
    archive = existing / 'market-data/historical-candle-archive-v1'
    verify(mount, device)
    archive.mkdir(parents=True, exist_ok=True)
    probe = archive / '.hcd01-rw-probe'
    raw = os.urandom(4096)
    atomic(probe, raw)
    assert probe.read_bytes() == raw
    probe.unlink()
    state = dict(mount=str(mount), device=device, volume_uuid=UUID, archive=str(archive),
                 volume_metadata=info, disk_usage=shutil.disk_usage(mount)._asdict(),
                 read_write_probe='PASS; fsync/write/read/delete 4096 bytes',
                 market_data_candidates=files, duplicate_content_groups=[v for v in duplicates.values() if len(v)>1],
                 unreadable_directories=errors, deduplication='inventory only; existing files preserved',
                 filesystem_notes='APFS journaled; case behavior not assumed; atomic replace on same volume; SQLite DELETE journal + FULL sync',
                 existing_store_children=sorted(p.name for p in existing.iterdir()))
    dump(OUT/'storage-audit.json', state)
    return state

class Archive:
    def __init__(self):
        self.mount, self.info = discover()
        self.device = self.mount.stat().st_dev
        self.root = self.mount / 'passport/trading-ai/market-data/historical-candle-archive-v1'
        self.guard()
        self.root.mkdir(parents=True, exist_ok=True)
        self.writer_lock=(self.root/'.writer.lock').open('a+b')
        try:
            fcntl.flock(self.writer_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('STOP_ARCHIVE_WRITER_BUSY; resume after current writer exits') from None
        self.db = sqlite3.connect(self.root / 'inventory.sqlite3')
        self.db.execute('PRAGMA busy_timeout=30000')
        self.db.execute('PRAGMA cache_size=-131072')
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS requests(
          request_id TEXT PRIMARY KEY, company_id INTEGER, instrument_key TEXT,
          unit TEXT, interval INTEGER, from_date TEXT, to_date TEXT,
          status TEXT, attempts INTEGER DEFAULT 0, raw_sha256 TEXT,
          raw_path TEXT, received_at TEXT, http_status INTEGER, row_count INTEGER,
          error_kind TEXT, elapsed_seconds REAL);
        CREATE INDEX IF NOT EXISTS request_gaps ON requests(status,company_id,unit,interval,from_date);
        CREATE TABLE IF NOT EXISTS datasets(
          dataset_id TEXT PRIMARY KEY, request_id TEXT, company_id INTEGER,
          instrument_key TEXT, timeframe TEXT, definition TEXT, observation_status TEXT,
          first_start TEXT,last_start TEXT,row_count INTEGER,parquet_path TEXT,
          parquet_sha256 TEXT,bytes INTEGER,raw_sha256 TEXT,observed_at TEXT,
          completeness TEXT,corporate_action_status TEXT,write_seconds REAL);
        CREATE INDEX IF NOT EXISTS candle_lookup ON datasets(company_id,timeframe,definition,observed_at);
        CREATE INDEX IF NOT EXISTS dataset_request ON datasets(request_id,definition,raw_sha256);
        CREATE TABLE IF NOT EXISTS revisions(
          request_id TEXT, previous_sha256 TEXT, new_sha256 TEXT, observed_at TEXT,
          PRIMARY KEY(request_id,new_sha256));
        CREATE TABLE IF NOT EXISTS candle_observations(
          company_id INTEGER,instrument_key TEXT,timeframe TEXT,definition TEXT,
          start TEXT,end_upper_bound TEXT,observed_at TEXT,status TEXT,
          quality TEXT,row_sha256 TEXT,dataset_id TEXT,
          PRIMARY KEY(instrument_key,timeframe,definition,start,observed_at));
        CREATE INDEX IF NOT EXISTS observation_asof ON candle_observations(company_id,timeframe,definition,observed_at,start);
        ''')

    def guard(self):
        verify(self.mount, self.device)
        if self.root.exists() and (self.root.is_symlink() or self.root.resolve().stat().st_dev != self.device):
            raise RuntimeError('STOP_ARCHIVE_NOT_ON_PASSPORT')

    def raw(self, raw):
        self.guard()
        digest = sha(raw)
        p = self.root / 'raw' / digest[:2] / (digest + '.json.gz')
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            atomic(p, gzip.compress(raw, mtime=0))
        if sha(gzip.decompress(p.read_bytes())) != digest:
            raise RuntimeError('STOP_RAW_CHECKSUM_MISMATCH')
        return digest, str(p.relative_to(self.root))

    def parquet(self, records, request, digest, received, definition='UPSTOX_NATIVE'):
        records=[dict(r,definition=definition) for r in records]
        groups={}
        for r in records:
            groups.setdefault((r['status'],r['quality']),[]).append(r)
        revision='DAILY_SESSION_AMBIGUITY_V1' if any(r['quality']=='AMBIGUOUS_DAILY_SESSION_BUCKET' for r in records) else ''
        current={dataset_identity(request['request_id'],digest,definition,status,quality,revision) for status,quality in groups}
        for ident, in self.db.execute("SELECT dataset_id FROM datasets WHERE request_id=? AND definition=? AND raw_sha256=? AND completeness NOT LIKE 'SUPERSEDED%'",
                                     (request['request_id'],definition,digest)).fetchall():
            if ident not in current:
                self.db.execute("UPDATE datasets SET completeness='SUPERSEDED_NORMALIZATION_V1; retained evidence only' WHERE dataset_id=?",(ident,))
        self.db.commit()
        return [self._parquet(part,request,digest,received,definition,status,quality,revision)
                for (status,quality),part in sorted(groups.items())]

    def _parquet(self, records, request, digest, received, definition,status,quality,context_revision=''):
        import pyarrow as pa
        import pyarrow.parquet as pq
        if not records:
            return None
        self.guard()
        ident = dataset_identity(request['request_id'],digest,definition,status,quality,context_revision)
        old = self.db.execute('SELECT parquet_path,parquet_sha256 FROM datasets WHERE dataset_id=?',(ident,)).fetchone()
        if old and sha((self.root/old[0]).read_bytes()) == old[1]:
            return ident
        p = self.root / ('provisional' if status=='PROVISIONAL' else 'normalized' if quality=='OHLC_VALID' else 'quarantine') / str(request['company_id']) / request['timeframe'] / (ident+'.parquet')
        p.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        tmp = p.with_suffix('.pending')
        prices=pa.decimal128(38,18)
        schema=pa.schema([
            ('company_id',pa.int64()),('instrument_key',pa.string()),('timeframe',pa.string()),
            ('definition',pa.string()),('provider_timestamp',pa.string()),('start',pa.string()),
            ('end',pa.string()),('end_upper_bound',pa.string()),('end_basis',pa.string()),
            ('open',prices),('high',prices),('low',prices),('close',prices),('volume',pa.int64()),
            ('open_interest',prices),('observed_at',pa.string()),('source',pa.string()),
            ('source_response_sha256',pa.string()),('status',pa.string()),('quality',pa.string()),
            ('historical_delivery_verified',pa.bool_()),('corporate_action_status',pa.string()),
            ('prefix_through',pa.string())])
        table = pa.Table.from_pylist(records,schema=schema).replace_schema_metadata({
            b'hcd01_version':NORMALIZATION_VERSION.encode(),b'volume_uuid':UUID.encode(),b'source_checksum':digest.encode()})
        pq.write_table(table, tmp, compression='zstd', use_dictionary=True, write_statistics=True)
        with tmp.open('rb') as f:
            os.fsync(f.fileno())
        self.guard()
        os.replace(tmp,p)
        fsync_directory(p.parent)
        checksum = sha(p.read_bytes())
        self.db.execute('INSERT OR REPLACE INTO datasets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,request['request_id'],request['company_id'],request['instrument_key'],request['timeframe'],definition,
             ','.join(sorted({r['status'] for r in records})), min(r['start'] for r in records), max(r['start'] for r in records),len(records),
             str(p.relative_to(self.root)),checksum,p.stat().st_size,digest,received,'CALENDAR_NOT_YET_CERTIFIED',
             'UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',time.perf_counter()-start))
        self.db.executemany('''INSERT INTO candle_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(instrument_key,timeframe,definition,start,observed_at)
            DO UPDATE SET dataset_id=excluded.dataset_id,quality=excluded.quality''',
            [(request['company_id'],request['instrument_key'],request['timeframe'],definition,r['start'],
              r['end_upper_bound'],received,r['status'],r['quality'],
              sha(json.dumps({k:r[k] for k in ('start','open','high','low','close','volume')},sort_keys=True,default=str).encode()),ident)
             for r in records])
        self.db.commit()
        return ident
