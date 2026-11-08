"""Read-only interrupted extraction reconciliation evidence."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot

db=Path('/data/scraper.db');guard=assert_safe_empty_wal(db)
with closing(sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True)) as c:
    c.row_factory=sqlite3.Row
    work=[dict(r) for r in c.execute('''SELECT w.*,ac.artifact_id,ac.byte_size,ac.content_sha256,b.local_path,
        ac.extraction_status,a.company_id,a.artifact_type FROM document_processing_work w
        JOIN artifact_contents ac USING(content_id) JOIN response_blobs b USING(blob_id)
        JOIN document_artifacts a USING(artifact_id) ORDER BY work_id DESC LIMIT 2''')]
    result={'work':work,'extractions':[dict(r) for r in c.execute('SELECT * FROM document_extractions ORDER BY extraction_id DESC LIMIT 2')],
        'source_requests':0,'production_writes':0,'guard':guard}
if snapshot(db)!=guard['files']:raise RuntimeError('DB changed during read-only inspection')
print(json.dumps(result))
