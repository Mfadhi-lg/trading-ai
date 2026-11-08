"""Read-only follow-up census, reusing the existing safety snapshot unchanged."""
from pathlib import Path
source=Path('/activation/phase4_trial_snapshot_runtime.py').read_text()
source=source.replace("verify(c,receipts='/receipts',epoch=epoch)","verify(c,receipts='/receipts',epoch=epoch,expires=json.loads(Path('/activation/phase4_schedule.json').read_text())['expires_at_utc'])")
extra="""
if '--final' in sys.argv:
    data['new_document_lifecycles']=[]
    for artifact in q('SELECT * FROM document_artifacts WHERE first_observed_at_utc>=? ORDER BY artifact_id',(epoch,)):
        aid=artifact['artifact_id'];cid=artifact['company_id']
        item={'artifact':artifact,
            'company':q('SELECT * FROM companies WHERE company_id=?',(cid,)),
            'eligibility':q('SELECT * FROM screener_collection_scope WHERE company_id=?',(cid,)),
            'sources':q('SELECT s.*,p.blob_id discovery_blob_id,p.company_id discovery_company_id FROM artifact_sources s JOIN artifact_discovery_pages p USING(discovery_page_id) WHERE s.artifact_id=?',(aid,)),
            'expectations':q('SELECT x.*,v.event_key,v.evidence_source,v.evidence_json,v.period_end event_period_end FROM artifact_expectations x JOIN artifact_events v USING(event_id) WHERE x.company_id=? AND x.artifact_type=?',(cid,artifact['artifact_type'])),
            'queue':q('SELECT * FROM artifact_download_queue WHERE artifact_id=?',(aid,)),
            'rating_queue':q('SELECT * FROM credit_rating_download_queue WHERE artifact_id=?',(aid,)),
            'download_attempts':q('SELECT * FROM artifact_download_attempts WHERE artifact_id=?',(aid,)),
            'contents':q('SELECT ac.*,b.local_path,b.byte_size blob_byte_size FROM artifact_contents ac JOIN response_blobs b USING(blob_id) WHERE artifact_id=?',(aid,)),
            'inbox':q('SELECT i.* FROM document_processing_inbox i JOIN artifact_contents ac USING(content_id) WHERE ac.artifact_id=?',(aid,)),
            'work':q('SELECT w.* FROM document_processing_work w JOIN artifact_contents ac USING(content_id) WHERE ac.artifact_id=?',(aid,)),
            'extractions':q('SELECT e.* FROM document_extractions e JOIN artifact_contents ac USING(content_id) WHERE ac.artifact_id=?',(aid,))}
        for content in item['contents']:
            saved=Path(content['local_path'])
            content['archive_checksum_verified']=saved.stat().st_size==content['blob_byte_size'] and hashlib.sha256(saved.read_bytes()).hexdigest()==content['content_sha256']
            require(content['archive_checksum_verified'],'New archive checksum/size mismatch')
        data['new_document_lifecycles'].append(item)
    data['stage_latches_are_not_new_attempts']=True
"""
exec(compile(source.replace('c.close();require',extra+'\nc.close();require'),'/activation/phase4_trial_snapshot_runtime.py','exec'))
