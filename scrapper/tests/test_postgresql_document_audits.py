"""Issue #99: document observations on disposable, restricted native logins."""
from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from scrapper.tests import test_postgresql_configuration as fixtures
from scrapper.tests import test_result_document_freshness as freshness_fixtures
NOW, BROADCAST = freshness_fixtures.NOW, freshness_fixtures.BROADCAST

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path[:0] = [str(ROOT/'ops/host'), str(ROOT/'ops/activation')]
import actual_download_audit as actual
import document_backlog_audit as backlog
import document_audit_reader as reader
import download_batch_evidence as batch
import download_failure_evidence as failure


def freshness_command():
    loader=importlib.machinery.SourceFileLoader('document_freshness_command',str(ROOT/'scripts/document-freshness'))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    return module


def populate(case):
    """Latest failed attempt on job 1; an unrelated newer success on job 2."""
    c=case.conn
    path=Path(case.temp.name)/'document.pdf';body=b'%PDF-synthetic'
    path.write_bytes(body);digest=hashlib.sha256(body).hexdigest()
    with c:
        c.execute("""INSERT INTO artifact_download_scopes(scope_id,scope_kind,artifact_types_json,created_at_utc,updated_at_utc)
            VALUES('audit','custom','["transcript"]',?,?)""",(NOW,NOW))
        for i in (1,2):
            c.execute("""INSERT INTO document_artifacts(artifact_id,company_id,artifact_key,artifact_type,title_raw,
                first_observed_at_utc,published_at_utc,period_end) VALUES(?,? ,?,'transcript','Synthetic',?,?,'2026-09-30')""",
                (i,case.cid,str(i),NOW,BROADCAST))
            c.execute("""INSERT INTO artifact_download_queue(download_job_id,artifact_id,requested_url,target_host,
                scope_id,status,created_at_utc,updated_at_utc) VALUES(?,? ,?,'issuer.example','audit',?, ?,?)""",
                (i,i,f'https://issuer.example/{i}?token=synthetic-secret','RETRYABLE_FAILURE' if i==1 else 'PENDING',NOW,NOW))
        c.execute("""INSERT INTO response_blobs(blob_id,content_sha256,byte_size,local_path,first_observed_at_utc,created_at_utc)
            VALUES(1,?,?,?,?,?)""",(digest,len(body),str(path),NOW,NOW))
        c.execute("""INSERT INTO artifact_contents(content_id,artifact_id,blob_id,media_type,content_sha256,byte_size,
            is_official,content_origin,created_at_utc) VALUES(1,2,1,'application/pdf',?,?,1,'publisher_original',?)""",(digest,len(body),NOW))
        c.execute("""INSERT INTO document_extractions(extraction_id,content_id,parser_name,parser_version,ocr_engine,
            ocr_engine_version,options_json,started_at_utc,completed_at_utc,extraction_status,page_or_slide_count,
            native_page_count,ocr_page_count,unusable_page_count,created_at_utc)
            VALUES(1,1,'synthetic','1','synthetic','1','{}',?,?,'ok',1,1,0,0,?)""",(NOW,NOW,NOW))
        text='éह\0tail'
        c.execute("""INSERT INTO document_pages(extraction_id,content_id,page_or_slide_number,page_kind,
            native_text,selected_text,selected_text_origin,native_text_usable)
            VALUES(1,1,1,'page',?,?,'native',1)""",(text,text))
        for attempt,job,status in ((1,1,'validated'),(2,1,'timed_out'),(3,2,'validated')):
            good=status=='validated'
            c.execute("""INSERT INTO artifact_download_attempts(download_attempt_id,artifact_id,requested_url,started_at_utc,
                fetched_at_utc,elapsed_ms,http_status,detected_media_type,content_sha256,blob_id,content_id,byte_size,result_status,limits_json)
                VALUES(?,?,?, ?,?,1000,?,?,?,?,?,?,?,'{}')""",(attempt,2 if good else job,
                f'https://issuer.example/{job}',NOW,NOW,200 if good else None,'application/pdf' if good else None,
                digest if good else None,1 if good else None,1 if good else None,len(body) if good else None,status))
            c.execute('INSERT INTO artifact_download_queue_attempts VALUES(?,?)',(job,attempt))
    return path


class OfflineDocumentAuditTests(unittest.TestCase):
    def test_exact_attempts_and_id_validation(self):
        case=freshness_fixtures.FreshResultTests();case.setUp();self.addCleanup(case.doCleanups)
        populate(case)
        self.assertEqual(batch.inspect(case.conn,[1]), [])  # Latest failure has no blob.
        self.assertEqual(batch.inspect(case.conn,[2])[0]['download_attempt_id'],3)
        self.assertEqual(failure.inspect(case.conn,[1])[0]['result_status'],'timed_out')
        self.assertEqual(backlog.page_metrics(case.conn,1,1)['text_characters'],2)
        for value in ([],[1,1],[True],[0],list(range(1,12))):
            with self.subTest(value=value),self.assertRaises(ValueError):batch.inspect(case.conn,value)

    def test_explicit_historical_snapshot_works_with_native_backend_selected(self):
        case=freshness_fixtures.FreshResultTests();case.setUp();self.addCleanup(case.doCleanups)
        snapshot=Path(case.temp.name)/'snapshot.db'
        from contextlib import closing
        with closing(sqlite3.connect(snapshot)) as dest:case.conn.backup(dest)
        before=hashlib.sha256(snapshot.read_bytes()).hexdigest();output=io.StringIO()
        with patch.dict(os.environ,{'TRADING_AI_DB_BACKEND':'postgresql'}),patch.object(sys,'argv',[
                'document-freshness','--snapshot',str(snapshot),'--since',BROADCAST]),redirect_stdout(output):
            freshness_command().main()
        result=json.loads(output.getvalue())
        self.assertEqual(result['database_source']['mode'],'historical_sqlite_snapshot')
        self.assertIn('snapshot_mtime_utc',result['database_source'])
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(),before)

    def test_html_signed_query_values_are_redacted_without_losing_hash(self):
        url='https://issuer.example/report?document=42&amp;token=synthetic-secret'
        result=backlog.redact_tree({'html':'<a href="'+url+'">Report</a>'})
        self.assertNotIn('synthetic-secret',result['html'])
        self.assertIn('document=42',result['html'])
        self.assertEqual(result['html_sha256'],hashlib.sha256(('<a href="'+url+'">Report</a>').encode()).hexdigest())

    def test_report_does_not_present_old_counts_as_current(self):
        from document_backlog_report import unpaired_observation
        text=unpaired_observation({'observed_at_utc':NOW,'source':{'mode':'native_current_observation'},
            'pending_associations':[{'status':'PENDING','eligible':True,'attached':True,'jobs':3}]},{'generic_jobs':7})
        self.assertIn('7 generic jobs and 3 eligible',text)
        self.assertIn(NOW,text)
        self.assertNotIn('22,214',text)

    def test_native_mode_requires_explicit_backend(self):
        with patch.dict(os.environ,{'TRADING_AI_DB_BACKEND':'sqlite'}),patch.object(sys,'argv',[
                'document-freshness','--native','--since',BROADCAST]),patch('sqlite3.connect') as connect:
            with self.assertRaises(SystemExit):freshness_command().main()
            connect.assert_not_called()

    def test_transport_uses_sealed_reader_and_readonly_document_mount(self):
        import postgresql_release
        prefix=['run','--network=none','--read-only','--env','TRADING_AI_DB_BACKEND=postgresql','--entrypoint','python','sha256:sealed']
        fallback=['docker','run','--entrypoint','python','old-image','/host/actual_download_audit.py']
        with patch.object(postgresql_release,'target',return_value={'image':'sha256:sealed'}),patch.object(
                postgresql_release,'verify') as verify,patch.object(postgresql_release,'reader_prefix',return_value=prefix):
            command=reader.container_command(fallback,evidence='/tmp/evidence',store='/tmp/documents')
        verify.assert_called_once()
        self.assertEqual(command[-4:],['--entrypoint','python','sha256:sealed','/host/actual_download_audit.py'])
        self.assertIn('type=bind,source=/tmp/documents,target=/documents,readonly',command)
        self.assertNotIn('old-image',command)


@unittest.skipUnless(os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'),'requires disposable PostgreSQL')
class NativeDocumentAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ConfigurationIntegrationTests.setUpClass.__func__(cls)
        cls.policy=staticmethod(lambda *_:sqlite3.SQLITE_DENY)

    drop_database=classmethod(fixtures.ConfigurationIntegrationTests.drop_database.__func__)
    admin_session=classmethod(fixtures.ConfigurationIntegrationTests.admin_session.__func__)
    cleanup=fixtures.ConfigurationIntegrationTests.cleanup
    dsn=fixtures.ConfigurationIntegrationTests.dsn

    def setUp(self):
        fixtures.ConfigurationIntegrationTests.setUp(self)

    @contextmanager
    def native(self, **changes):
        environment={'TRADING_AI_ENV':'test','TRADING_AI_DB_BACKEND':'postgresql','DATABASE_URL':self.dsn(),
            'TRADING_AI_POSTGRESQL_SCHEMA':self.schema,'TRADING_AI_POSTGRESQL_TARGET_ID':self.target_id,**changes}
        with patch.dict(os.environ,environment),patch('sqlite3.connect',side_effect=AssertionError('SQLite opened')):
            yield

    def test_full_census_and_actual_audit_native_no_sqlite(self):
        from scrapper.db.documents.processing import ProcessingVersion
        version=ProcessingVersion.create(parser_name='synthetic',parser_version='1',ocr_engine='synthetic',ocr_engine_version='1',options={})
        out=self.root/'evidence';out.mkdir();(out/'raw').mkdir()
        with self.native(),patch('scrapper.pipeline.extract_document.processing_version',return_value=version):
            self.assertEqual(backlog.census(Path('/unused/stale.db'),out)[0]['contents'],0)
            with reader.observation(None) as (conn,source):
                report=actual.audit(conn,out,source)
                self.assertEqual(conn.execute('SHOW transaction_read_only').fetchone()[0],'on')
                self.assertEqual(conn.execute('SHOW transaction_isolation').fetchone()[0],'repeatable read')
                self.assertFalse(conn.execute("SELECT has_table_privilege(current_user,'companies','INSERT')").fetchone()[0])
                self.assertEqual(report['fresh_download_attempts'],[])
        inventory=json.loads((out/'inventory.json').read_text())
        self.assertEqual(inventory['source']['mode'],'native_current_observation')
        self.assertEqual(inventory['historical_comparison']['status'],'not_opened_in_native_mode')

    def test_attempt_failure_and_freshness_match_sqlite(self):
        from scrapper.db.documents.freshness import acquisition_freshness_report
        from scrapper.db.postgresql import PostgreSQLSettings
        from scrapper.db.postgresql_transfer import export_snapshot, transfer
        case=freshness_fixtures.FreshResultTests();case.setUp();self.addCleanup(case.doCleanups)
        populate(case)
        expected_batch=batch.inspect(case.conn,[1,2]);expected_failure=failure.inspect(case.conn,[1,2])
        expected_pages=backlog.page_metrics(case.conn,1,1)
        expected_freshness=acquisition_freshness_report(case.conn,since_utc=BROADCAST,limit=1)
        source=Path(case.temp.name)/'isolated.sqlite3'
        receipt=export_snapshot(source,self.root/'source.db')
        transfer(PostgreSQLSettings(self.dsn(admin=True)),self.root/'source.db',self.schema,expected_sha256=receipt['snapshot_sha256'])
        with self.native(),reader.observation(None) as (conn,_):
            self.assertEqual(backlog.page_metrics(conn,1,1),expected_pages)
            self.assertEqual(batch.inspect(conn,[1,2]),expected_batch)
            self.assertEqual(failure.inspect(conn,[1,2]),expected_failure)
            self.assertEqual(acquisition_freshness_report(conn,since_utc=BROADCAST,limit=1),expected_freshness)
        from scrapper.db.documents.processing import ProcessingVersion
        version=ProcessingVersion.create(parser_name='synthetic',parser_version='1',ocr_engine='synthetic',ocr_engine_version='1',options={})
        old=self.root/'sqlite-evidence';old.mkdir();(old/'raw').mkdir()
        new=self.root/'native-evidence';new.mkdir();(new/'raw').mkdir()
        @contextmanager
        def source_observation(*args,**kwargs):yield case.conn, {'mode':'test_sqlite_snapshot'}
        with patch.object(reader,'observation',source_observation),patch('scrapper.pipeline.extract_document.processing_version',return_value=version):
            old_totals=backlog.census(source,old)
        with self.native(),patch('scrapper.pipeline.extract_document.processing_version',return_value=version):
            self.assertEqual(backlog.census(None,new),old_totals)
            with reader.observation(None) as (conn,source_info),patch.object(backlog,'verify',side_effect=lambda row: {'state':'synthetic'}):
                report=actual.audit(conn,new,source_info)
                self.assertEqual([r['download_job_id'] for r in report['candidates']],[1])
        old_inventory=json.loads((old/'inventory.json').read_text());new_inventory=json.loads((new/'inventory.json').read_text())
        for key in ('pending_associations','financial_scope_source_aliases','permitted_fresh_download_selection'):
            self.assertEqual(new_inventory[key],old_inventory[key])
        self.assertEqual(json.loads((new/'jobs.jsonl').read_text().splitlines()[0])['requested_url_sha256'],
                         hashlib.sha256(b'https://issuer.example/1?token=synthetic-secret').hexdigest())
        self.assertNotIn('synthetic-secret',(new/'DOWNLOAD_CANDIDATES.csv').read_text())
        output=io.StringIO()
        with self.native(),patch.object(sys,'argv',['document-freshness','--native','--since',BROADCAST,'--limit','1']),redirect_stdout(output):
            freshness_command().main()
        result=json.loads(output.getvalue());self.assertEqual(result.pop('database_source')['backend'],'postgresql')
        self.assertEqual(result,expected_freshness)

    def test_unavailable_native_or_mismatched_target_fails_without_fallback(self):
        for changes in ({'DATABASE_URL':'postgresql://synthetic@127.0.0.1:1/unavailable'},
                        {'TRADING_AI_POSTGRESQL_TARGET_ID':'00000000-0000-4000-8000-000000000001'}):
            with self.subTest(changes=tuple(changes)),self.native(**changes),self.assertRaises(Exception):
                with reader.observation(None):pass
            output=io.StringIO()
            with self.native(**changes),patch.object(sys,'argv',['document-freshness','--native','--since',BROADCAST]),redirect_stdout(output):
                self.assertEqual(freshness_command().main(),1)
            self.assertEqual(json.loads(output.getvalue())['status'],'unknown')
