"""Read-only selectors against actual production and current candidate source."""
from pathlib import Path
import sys,sqlite3,json
from datetime import datetime,timezone
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal,snapshot
from scrapper.db.database import check_schema_compatibility
from scrapper.operations.workers.automatic_documents import select_downloads
from scrapper.db.documents.processing import automatic_processing_contents
from scrapper.pipeline.extract_document import processing_version
from scrapper.documents.ocr import TesseractOcr
from scrapper.sources.documents.routine import company_pages
from phase4_schedule import settings
path=Path('/data/scraper.db');before=assert_safe_empty_wal(path)
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
c.execute('PRAGMA query_only=ON');check_schema_compatibility(c)
now=datetime.now(timezone.utc).isoformat()
version=processing_version(TesseractOcr())
companies=[r[0] for r in c.execute("SELECT company_id FROM screener_collection_scope WHERE eligibility='eligible' AND company_id IS NOT NULL ORDER BY company_id LIMIT 3")]
data={'status':'PASS','observed_at_utc':now,'config':settings(),
    'initial_download_selection':select_downloads(c,now=now,epoch=now,limit=1),
    'initial_processing_selection':automatic_processing_contents(c,version=version,now=now,epoch=now,limit=1),
    'sample_existing_source_pages':{cid:company_pages(c,cid) for cid in companies},
    'processing_version':version.values(),'source_requests':0,'writes':0}
c.close()
if snapshot(path)!=before['files']:raise RuntimeError('Production sidecars changed during preflight')
print(json.dumps(data))
