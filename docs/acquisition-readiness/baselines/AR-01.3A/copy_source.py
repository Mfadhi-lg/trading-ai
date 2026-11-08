"""Read-only source snapshot; never opens production SQLite or replays WAL."""
import hashlib
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def source_state():
    p = Path('/source/scraper.db')
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise RuntimeError('Source is not an existing regular main DB')
    sidecars = {suffix: os.path.lexists(str(p) + suffix) for suffix in ('-wal', '-shm', '-journal')}
    if any(sidecars.values()):
        raise RuntimeError('Unexpected source sidecar; stop nominal WAL-free snapshot branch: ' + str(sidecars))
    return {'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
            'device': s.st_dev, 'inode': s.st_ino, 'sidecars': sidecars}


def main():
    gate = json.loads(Path('/proof/snapshot-gate.json').read_text())
    gate_age = time.time() - gate['observed_at_epoch']
    if not gate['writer_gate_passed'] or not 0 <= gate_age <= 10:
        raise RuntimeError('Immediate source writer gate absent/stale')
    if sha(Path(__file__)) != gate['copy_script_sha256']:
        raise RuntimeError('Copy script identity mismatch')
    mounts = [line.split(' - ', 1)[0].split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    if not any(f[4] == '/source' and 'ro' in f[5].split(',') for f in mounts):
        raise RuntimeError('Production source is not mounted read-only')
    before = source_state()
    source = Path('/source/scraper.db')
    before_hash = sha(source)
    if source_state() != before:
        raise RuntimeError('Source changed during initial hash')
    result = {'source_before': before, 'source_sha256_before': before_hash,
              'writer_gate_age_seconds_at_start': gate_age,
              'source_mount': 'read-only', 'production_sqlite_opened': False}
    if '--verify-only' not in sys.argv:
        target = Path('/snapshot/raw/scraper.db')
        target.parent.mkdir()
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        result['snapshot_sha256'] = sha(target)
        result['snapshot_bytes'] = target.stat().st_size
        if result['snapshot_sha256'] != before_hash:
            raise RuntimeError('Source/snapshot byte hash mismatch')
        target.chmod(0o444)
    after_hash = sha(source)
    after = source_state()
    if after != before or after_hash != before_hash:
        raise RuntimeError('Source identity/hash/sidecars changed; retained copy not valid')
    result.update({'source_after': after, 'source_sha256_after': after_hash, 'status': 'PASS'})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
