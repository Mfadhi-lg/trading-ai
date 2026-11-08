"""Networkless finite Phase 2 adapter around the existing production worker."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import signal
import sys
sys.path[:0]=['/activation','/app']


def main():
    from nse_results_runtime import run_owned, require
    from local_processing_network import assert_isolated
    from phase4_routine_runtime import routine_authorizer
    from scrapper.db.database import connect
    from scrapper.documents.ocr import TesseractOcr
    from scrapper.documents.store import assert_document_store_ready
    from scrapper.operations.workers.local_document_campaign import run_local_campaign, digest
    from scrapper.operations.coordinator.exchange import ownership
    plan=json.loads(Path('/selected/plan.json').read_text())
    package=json.loads(Path('/selected/manifest.json').read_text())
    campaign=json.loads(Path('/campaign/PILOT_AUTHORIZATION_AND_PLAN.json').read_text())
    require(plan['authorized'] and plan['stage']=='DOCUMENT_PROCESSING','Processing authorization required')
    require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()==package['plan_sha256'],'Plan drift')
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==package['runtime_sha256'],'Runtime drift')
    for name,value in package['application_hashes'].items():
        require(hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==value,'Application drift: '+name)
    require(digest(campaign)==plan['campaign_sha256'],'Finite manifest drift')
    interfaces=assert_isolated()
    capacity=json.loads(Path('/selected/execution-storage-preflight.json').read_text())
    require(hashlib.sha256(Path('/physical_documents/.trading_ai_runtime_identity.json').read_bytes()).hexdigest()==capacity['physical_identity_sha256'],'Physical archive identity changed')
    db=Path('/data/scraper.db')
    run_owned((),db,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
    assert_document_store_ready()
    def guarded(path):
        require(Path(path)==db,'Wrong database')
        c=connect(path);c.set_authorizer(routine_authorizer);return c
    class LocalDeadline(BaseException):pass
    def deadline(*_):raise LocalDeadline('Finite local campaign deadline')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(plan['campaign_seconds'])
    with ownership(db,'automatic-document-processing') as acquired:
        require(acquired,'Canonical processing ownership busy')
        result=run_local_campaign(db_path=db,manifest=campaign,expected_sha256=plan['campaign_sha256'],
            ocr_engine=TesseractOcr(),output_dir=Path('/campaign/receipts'),connection_factory=guarded,
            limit=plan['campaign_limit'],seconds=max(1,plan['campaign_seconds']-30))
    signal.alarm(0)
    with closing(guarded(db)) as c:
        checks=[]
        for receipt in result['outcomes']:
            if 'extraction_id' not in receipt:continue
            extraction=c.execute('SELECT * FROM document_extractions WHERE extraction_id=?',(receipt['extraction_id'],)).fetchone()
            require(extraction['content_id']==receipt['content_id'],'Cross-content extraction')
            counts=c.execute('''SELECT count(*) pages,sum(length(selected_text)) text_characters,
                min(page_or_slide_number) first_page,max(page_or_slide_number) last_page FROM document_pages WHERE extraction_id=?''',
                (receipt['extraction_id'],)).fetchone()
            checks.append({'content_id':receipt['content_id'],'extraction_id':receipt['extraction_id'],**dict(counts)})
    print(json.dumps({'stage':'local_campaign_outcome','result':result,'validation':checks,
        'interfaces':interfaces,'network_mode':'none','external_requests':0}),flush=True)


if __name__=='__main__':main()
