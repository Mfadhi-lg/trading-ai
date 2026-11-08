"""Isolated synthetic discovery fixture using the real retained issuer PDF.

No production DB/mounts. Synthetic HTML is explicitly a test input, not a claim
that an issuer landing page was fetched. The original URL and PDF are real.
"""
import argparse,hashlib,json,tempfile,sqlite3
from phase4_documents_runtime import WRITE_TABLES
from pathlib import Path
from scrapper.db.database import connect,init_db
from scrapper.sources.documents.discovery import build_discovery_plan
from scrapper.db.document_artifact_repo import persist_discovery_plan
from scrapper.db.documents.expectations import ensure_result_artifact_expectations
from scrapper.pipeline.accepted_document_downloads import enqueue_discovered_documents
from scrapper.operations.workers.automatic_documents import select_downloads
from scrapper.pipeline.run_document_download_worker import run_document_download_worker
from scrapper.operations.workers.document_processing import run_automatic_processing
from scrapper.tests.test_document_download_queue import FakeSession,FakeResponse,FakeHealth
from scrapper.tests.test_d03_document_extraction import FakeOcr

NOW='2026-10-11T15:00:00+00:00'
EPOCH='2026-10-09T00:00:00+00:00'
URL='https://www.sacheerome.com/pdf/H2_FY_26_Earning_Call_Transcript.pdf'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pdf',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    body=a.pdf.read_bytes();digest=hashlib.sha256(body).hexdigest()
    if digest!='f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3':raise ValueError('Unexpected real source bytes')
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);db=root/'db.sqlite';c=connect(db);init_db(c,apply_migrations=True)
        (root/'fixture.html').write_text(f'<a href="{URL}" data-artifact-type="transcript" data-period-end="2026-03-31" data-event-date="2026-06-01">H2 FY26 Earnings Call Transcript</a>')
        manifest={'schema_version':'1.0.0','symbol':'SACHEEROME','company_name':'Sacheerome Limited',
            'pages':[{'source':'company_ir','page_url':'https://www.sacheerome.com/','publisher_claim':'Sacheerome Limited','content_path':'fixture.html'}]}
        (root/'manifest.json').write_text(json.dumps(manifest))
        plan=build_discovery_plan(root/'manifest.json',requested_symbol='SACHEEROME')
        saved=persist_discovery_plan(c,plan,observed_at_utc=NOW,snapshot_root=root/'snapshots')
        company=c.execute('SELECT company_id FROM companies').fetchone()[0]
        with c:
            c.execute("INSERT INTO screener_collection_scope(company_path,company_id,eligibility,floor_crore,observed_at_utc,evidence_json,next_check_at_utc) VALUES (?,?,'eligible','580',?,'{}',?)",('/company/SACHEEROME/',company,NOW,NOW))
        expectations=ensure_result_artifact_expectations(c,company_id=company,filing_id=1,exchange='NSE',exchange_key='fixture',
            result_period='2026-03',period_end='2026-03-31',broadcast_at_utc=NOW,created_at_utc=NOW)
        enqueued=enqueue_discovered_documents(c,epoch=EPOCH,now=NOW)
        selected=select_downloads(c,now=NOW,epoch=EPOCH)
        assert len(selected)==1 and selected[0][1]==2
        def guarded(path):
            conn=connect(path)
            def authorize(action,table,column,database,trigger):
                if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                    if table not in WRITE_TABLES or (action==sqlite3.SQLITE_DELETE and table!='acquisition_host_permits'):
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            return conn
        c.close();c=guarded(db)
        session=FakeSession({URL:[FakeResponse(body,url=URL)]})
        download=run_document_download_worker(c,limit=1,allowed_job_ids=(selected[0][0]['download_job_id'],),
            artifact_types=('transcript',),session_factory=lambda:session,health_check=FakeHealth,
            snapshot_root=root/'archive',document_store=root,throttle_seconds=0,now=lambda:NOW)
        content=dict(c.execute('SELECT * FROM artifact_contents').fetchone());assert content['content_sha256']==digest
        assert c.execute('SELECT count(*) FROM document_processing_inbox').fetchone()[0]==1
        outcome=run_automatic_processing(db_path=db,epoch=EPOCH,ocr_engine=FakeOcr(),output_dir=root/'output',now_utc=lambda:NOW,connection_factory=guarded)
        assert outcome['outcomes'][0]['extraction_status']=='ok'
        pages=c.execute('SELECT count(*) FROM document_pages').fetchone()[0];assert pages==21
        assert run_automatic_processing(db_path=db,epoch=EPOCH,ocr_engine=FakeOcr(),output_dir=root/'output',now_utc=lambda:NOW,connection_factory=guarded)['status']=='idle'
        assert enqueue_discovered_documents(c,epoch=EPOCH,now=NOW)==[]
        queue=dict(c.execute('SELECT * FROM artifact_download_queue').fetchone());assert queue['status']=='SUCCEEDED'
        receipt={'status':'PASS','database':'isolated disposable schema075 fixture','discovery_page':'synthetic fixture; source period derived from real PDF cover',
            'real_pdf_url':URL,'sha256':digest,'bytes':len(body),'pages':pages,'expectation_ids':list(expectations),
            'enqueue':enqueued,'download':download,'content':content,'processing':outcome,
            'queue':queue,'source_requests_this_validation':0,'production_writes':0,'idempotent_second_tick':'idle'}
        c.close()
        a.output.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'status':'PASS','pages':pages,'receipt':str(a.output)}))


if __name__=='__main__':main()
