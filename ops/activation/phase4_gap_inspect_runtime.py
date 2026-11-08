"""Read-only targeted lineage census, using the existing guarded snapshot."""
from pathlib import Path
source=Path('/activation/phase4_trial_snapshot_runtime.py').read_text()
injection="""
data['poonawalla']={
 'company':q('SELECT * FROM companies WHERE company_id=83'),
 'artifact':q('SELECT * FROM document_artifacts WHERE artifact_id=83279'),
 'sources':q('SELECT * FROM artifact_sources WHERE artifact_id=83279'),
 'pages':q('SELECT * FROM artifact_discovery_pages WHERE discovery_page_id=14766'),
 'expectations':q('SELECT expectation.*,event.event_key,event.evidence_source,event.evidence_json,event.period_end FROM artifact_expectations expectation JOIN artifact_events event USING(event_id) WHERE expectation.company_id=83'),
 'queue':q('SELECT * FROM artifact_download_queue WHERE artifact_id=83279'),
 'rating_queue':q('SELECT * FROM credit_rating_download_queue WHERE artifact_id=83279'),
 'contents':q('SELECT * FROM artifact_contents WHERE artifact_id=83279'),
 'attempts':q('SELECT * FROM artifact_download_attempts WHERE artifact_id=83279'),
 'inbox':q('SELECT i.* FROM document_processing_inbox i JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=83279'),
 'work':q('SELECT w.* FROM document_processing_work w JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=83279'),
 'extractions':q('SELECT e.* FROM document_extractions e JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=83279'),
 'scope':q('SELECT * FROM screener_collection_scope WHERE company_id=83'),
 'rating_health':q('SELECT * FROM credit_rating_host_health WHERE host=?',('poonawallafincorp.com',)),
 'rating_policy':q('SELECT * FROM credit_rating_host_policy WHERE host=?',('poonawallafincorp.com',)),
 'health':q('SELECT * FROM artifact_download_host_health WHERE host LIKE ? OR host LIKE ?',('%poonawalla%','%bse%'))}
data['poonawalla_care_report']={
 'artifact':q('SELECT * FROM document_artifacts WHERE artifact_id=83267'),
 'sources':q('SELECT * FROM artifact_sources WHERE artifact_id=83267'),
 'rating_queue':q('SELECT * FROM credit_rating_download_queue WHERE artifact_id=83267'),
 'contents':q('SELECT c.*,b.local_path,b.byte_size FROM artifact_contents c JOIN response_blobs b USING(blob_id) WHERE artifact_id=83267'),
 'inbox':q('SELECT i.* FROM document_processing_inbox i JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=83267'),
 'work':q('SELECT w.* FROM document_processing_work w JOIN artifact_contents c USING(content_id) WHERE c.artifact_id=83267'),
 'classification':q('SELECT * FROM artifact_source_evidence_classifications WHERE content_id=78207')}
for content in data['poonawalla_care_report']['contents']:
 path=Path(content['local_path']);content['stored_checksum_verified']=path.stat().st_size==content['byte_size'] and hashlib.sha256(path.read_bytes()).hexdigest()==content['content_sha256']
data['poonawalla']['reviewed_host_policy']=vars(reviewed_policy(c,'poonawallafincorp.com'))
from scrapper.operations.workers.automatic_documents import select_downloads
cfg=json.loads(Path('/activation/phase4_schedule.json').read_text())
data['eligible_download_selection']=[dict(r) for r,lane in select_downloads(c,now=now,epoch=cfg['activated_at_utc'],limit=1,historical_limit=0)]
"""
source=source.replace("verify(c,receipts='/receipts',epoch=epoch)","verify(c,receipts='/receipts',epoch=epoch,expires=json.loads(Path('/activation/phase4_schedule.json').read_text())['expires_at_utc'])") if 'expires=None' in Path('/activation/verify_phase4_unattended.py').read_text() else source
exec(compile(source.replace('c.close();require',injection+'\nc.close();require'),'/activation/phase4_trial_snapshot_runtime.py','exec'))
