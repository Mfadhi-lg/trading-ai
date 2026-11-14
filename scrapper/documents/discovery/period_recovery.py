"""Bounded, resumable metadata recovery from already saved document evidence."""
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import sys

from .promotion import fill_stored_artifact_period


def saved_pdf_headers(conn, artifact_id, *, timeout):
    from ...config import DOCUMENT_STORE, FILES, SNAPSHOTS
    row = conn.execute('''SELECT c.content_id,c.byte_size,c.content_sha256,b.local_path
        FROM artifact_contents c JOIN response_blobs b USING(blob_id)
        JOIN document_artifacts a ON a.artifact_id=c.artifact_id
        WHERE c.artifact_id=? AND c.media_type='application/pdf'
          AND c.content_origin='publisher_original'
        ORDER BY (c.content_id=a.preferred_content_id) DESC,c.content_id DESC LIMIT 1''',
        (artifact_id,)).fetchone()
    if not row or row['byte_size'] > 32*1024**2:
        return []
    item = dict(row)
    path = Path(item['local_path']).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in (DOCUMENT_STORE, FILES, SNAPSHOTS)):
        return []
    item['local_path'] = str(path)
    try:
        result = subprocess.run([sys.executable, '-m',
            'scrapper.documents.discovery.native_period_header'], input=json.dumps(item),
            capture_output=True, text=True, timeout=timeout, check=True)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def recover_period_batch(conn, *, state_path, limit=100, seconds=5,
                         corrected_at_utc=None, stopped=lambda: False, legacy_state_path=None):
    """Call under the automatic processing owner; never download or re-extract.

    A fixed high watermark ensures new arrivals cannot starve earlier unresolved
    rows. Every completed sweep starts again so later companion evidence and
    improved recognition rules also reach previously examined documents.
    Corrections commit before the cursor: interruption safely replays work.
    """
    if type(limit) is not int or not 1 <= limit <= 1000 or seconds <= 0:
        raise ValueError('Positive bounded recovery budget required')
    state_path = Path(state_path)
    source=state_path if state_path.exists() else Path(legacy_state_path) if legacy_state_path else state_path
    state = json.loads(source.read_text()) if source.exists() else {}
    after = state.get('after_artifact_id', 0)
    ceiling = state.get('through_artifact_id', 0)
    if any(type(value) is not int or value < 0 for value in (after, ceiling)) or after > ceiling:
        raise ValueError('Invalid period recovery cursor')
    if not ceiling:
        ceiling = conn.execute('SELECT coalesce(max(artifact_id),0) FROM document_artifacts').fetchone()[0]
    candidates = list(conn.execute('''SELECT artifact_id FROM document_artifacts
        WHERE artifact_id>? AND artifact_id<=? AND period_end IS NULL
          AND artifact_type<>'credit_rating' ORDER BY artifact_id LIMIT ?''',
        (after, ceiling, limit)))
    started = time.monotonic()
    checked = recovered = 0
    for row in candidates:
        if stopped() or time.monotonic()-started >= seconds:
            break
        filled = fill_stored_artifact_period(conn, row['artifact_id'],
                                            corrected_at_utc=corrected_at_utc)
        remaining = seconds-(time.monotonic()-started)
        if not filled and remaining > .1:
            headers = saved_pdf_headers(conn, row['artifact_id'], timeout=min(2, remaining))
            if headers:
                filled = fill_stored_artifact_period(conn, row['artifact_id'],
                    corrected_at_utc=corrected_at_utc, extra_texts=headers)
        recovered += int(filled)
        after = row['artifact_id']
        checked += 1
    complete = checked == len(candidates) and len(candidates) < limit
    state = {'after_artifact_id': 0 if complete else after,
             'through_artifact_id': 0 if complete else ceiling}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=state_path.parent,
                                     prefix=state_path.name+'.', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, state_path)
        descriptor = os.open(state_path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return {'checked': checked, 'recovered': recovered, 'sweep_complete': complete, **state}
