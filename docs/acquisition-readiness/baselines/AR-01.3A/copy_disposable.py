"""Seed a new disposable volume from a retained verified snapshot, not production."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


source, target = Path('/input/scraper.db'), Path('/output/scraper.db')
expected = sys.argv[1]
if sha(source) != expected:
    raise RuntimeError('Verified snapshot input hash mismatch')
with source.open('rb') as src, target.open('xb') as dst:
    shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
    dst.flush()
    os.fsync(dst.fileno())
target.chmod(0o444)
actual = sha(target)
if actual != expected or sha(source) != expected:
    raise RuntimeError('Disposable copy/input hash mismatch')
print(json.dumps({'status': 'PASS', 'input_sha256': expected, 'output_sha256': actual,
                  'bytes': target.stat().st_size, 'inode': target.stat().st_ino,
                  'mode': oct(target.stat().st_mode & 0o777), 'production_mounts': []}, indent=2))
