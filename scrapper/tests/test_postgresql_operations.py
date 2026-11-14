"""Operator observations match SQLite using synthetic rows and a disposable PG schema."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'ops/activation'),str(Path(__file__).resolve().parents[2]/'ops/host')]
import acquisition_queries as queries
from scrapper.tests.postgresql_fixtures import DisposablePostgreSQLTestCase
from scrapper.tests.test_postgresql_transfer import make_source
from scrapper.db.postgresql_transfer import export_snapshot,transfer

class NativeObservationTests(DisposablePostgreSQLTestCase):
    def test_queries_match_sqlite_and_keep_read_only_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.db';snapshot=Path(directory)/'snapshot.db'
            sqlite=make_source(source);sqlite.row_factory=sqlite3.Row
            self.addCleanup(sqlite.close)
            sqlite.executemany("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value) VALUES(91,'synthetic',?)",
                               [('Z symbol',),('A symbol',),('₹ symbol',)])
            sqlite.execute("INSERT INTO accepted_page_versions VALUES(91,'synthetic','standalone','fixture',41,'2026-11-08T00:00:00Z','fixture','fixture')")
            sqlite.executemany("""INSERT INTO financial_observations
                (parse_id,reporting_scope,section_code,line_item_raw,row_position,period_label_raw,period_end,
                 period_type,column_position,first_observed_at_utc,available_at_utc,availability_basis,display_value)
                VALUES(41,'standalone',?,'fixture',?,?,?,'quarter',0,'2026-11-08T00:00:00Z',
                       '2026-11-08T00:00:00Z','fixture','1')""",
                [('z_section',0,'undated',None),('z_section',1,'dated','2026-09-30'),
                 ('a_section',2,'dated','2026-09-30')])
            sqlite.commit()
            self.assertEqual(queries.query(sqlite,'company_catalog')['companies'][0]['identifiers'],
                             'A symbol Z symbol ₹ symbol')
            self.assertEqual([(row['period_end'],row['section_code']) for row in queries.query(sqlite,'company',company_id=91)['periods']],
                             [('2026-09-30','a_section'),('2026-09-30','z_section'),(None,'z_section')])
            report=export_snapshot(source,snapshot)
            transfer(self.settings,snapshot,self.schema,expected_sha256=report['snapshot_sha256'])
            with self.session(read_only=True) as native:
                for action,args in (
                    ('summary',{}),('download_diagnostics',{}),('company_catalog',{}),
                    ('search',{'term':'Synthetic'}),('search',{'term':'₹'}),
                    ('company',{'company_id':91}),
                    ('quarter_monitor',{'quarter':'2026-09'}),
                    ('quarter_monitor',{'quarter':'unknown'}),
                    ('quarter_coverage_export',{'quarter':'all'})):
                    with self.subTest(action=action,args=args):
                        expected=queries.query(sqlite,action,**args)
                        actual=queries.query(native,action,**args)
                        if 'query_seconds' in expected:
                            expected.pop('query_seconds');actual.pop('query_seconds')
                        self.assertEqual(actual,expected)
                        import json
                        json.dumps(actual)  # Native integer aggregates must stay serializable.
                with self.assertRaises(sqlite3.OperationalError):
                    native.execute("INSERT INTO companies(current_legal_name) VALUES ('forbidden')")

    def test_native_read_snapshot_preflight_and_preservation_digest(self):
        from unittest.mock import patch
        import postgresql_runtime as runtime
        from production_ownership import assert_no_conflicting_leases
        with self.session() as writer:
            writer.execute("INSERT INTO document_extraction_claims VALUES ('control','fixture','token','RELEASED','2026-01-01T00:00:00Z')")
            control=dict(writer.execute("SELECT * FROM document_extraction_claims WHERE resource_key='control'").fetchone())
            original=runtime.fingerprint(writer,'document_extraction_claims')
            writer.execute("UPDATE document_extraction_claims SET owner='different' WHERE resource_key='control'")
            self.assertNotEqual(original,runtime.fingerprint(writer,'document_extraction_claims'))
            writer.execute("UPDATE document_extraction_claims SET owner='fixture' WHERE resource_key='control'")
            writer.commit()
        def factory(*args,**kwargs):return self.connection(read_only=True)
        with patch.object(runtime,'enabled',return_value=True),patch.object(runtime,'verify_physical_storage'),patch('scrapper.config.assert_configured_writable_targets'),patch('scrapper.db.database.connect',side_effect=factory):
            with closing(runtime.read_connection(Path('/logical/app.db'))) as reader:
                self.assertEqual(reader.execute('SHOW transaction_isolation').fetchone()[0],'repeatable read')
                self.assertEqual(reader.execute('SHOW transaction_read_only').fetchone()[0],'on')
                self.assertIn('document_extraction_claims',runtime.table_names(reader))
                self.assertIn('resource_key',runtime.table_columns(reader,'document_extraction_claims'))
                assert_no_conflicting_leases(reader,now='2026-11-10T00:00:00Z')
            result=runtime.preflight(Path('/logical/app.db'),{'control_row':control})
            self.assertEqual(result['queue_count'],0)
            bad={**control,'owner':'wrong'}
            with self.assertRaisesRegex(RuntimeError,'control receipt'):
                runtime.preflight(Path('/logical/app.db'),{'control_row':bad})
        with self.session() as writer:
            writer.execute("UPDATE document_extraction_claims SET status='ACTIVE',lease_expires_at_utc='2999-01-01T00:00:00Z'")
            writer.commit()
        with self.session(read_only=True) as reader,self.assertRaisesRegex(RuntimeError,'Unexpired'):
            assert_no_conflicting_leases(reader,now='2026-11-10T00:00:00Z')

    def test_native_ingestion_never_invokes_sqlite_sidecar_guard(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import nse_results_runtime as runtime
        preview={'validation':{'status':'passed'},'accepted_row_count':1,'enqueue':{'queued':0},'snapshot_id':1}
        response=SimpleNamespace(content=b'synthetic response',status_code=200,fetch_status='ok',
            requested_url='https://synthetic.invalid',final_url='https://synthetic.invalid')
        def factory(*args,**kwargs):return self.connection(read_only=kwargs.get('read_only',False))
        with patch.dict(os.environ,{'TRADING_AI_DB_BACKEND':'postgresql'}), \
             patch('scrapper.db.database.connect',side_effect=factory), \
             patch('postgresql_runtime.preflight',return_value={'holds':[],'queue_count':0,'filing_counts':[]}), \
             patch('sqlite_sidecar_guard.assert_safe_empty_wal',side_effect=AssertionError('SQLite guard called')), \
             patch.object(runtime,'fetch_nse_response',return_value=response) as fetch, \
             patch('scrapper.workflows.exchange.universe.discover_exchange_results',return_value=preview) as discover:
            self.assertEqual(runtime.run_owned(('2026-09',),Path('/data/scraper.db'),{}),0)
            fetch.assert_called_once()
            self.assertEqual(discover.call_count,2)
            self.assertTrue(discover.call_args_list[0].kwargs['dry_run'])
            self.assertEqual(discover.call_args_list[1].kwargs['max_new_queue_entries'],100)


class NativeReleaseWiringTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import postgresql_release as release
        import json,uuid
        self.release=release
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);here=self.root/'ops/activation';here.mkdir(parents=True)
        (self.root/'scrapper').mkdir();(self.root/'ops/host').mkdir()
        (self.root/'scrapper/example.py').write_text('pass\n');(here/'operator.py').write_text('pass\n')
        patcher=patch.multiple(release,ROOT=self.root,HERE=here);patcher.start();self.addCleanup(patcher.stop)
        self.target={'format':1,'database':'trading_ai','schema':'trading_ai','roles':release.ROLES,
            'image':'sha256:'+'a'*64,'postgresql_image':'postgres@sha256:'+'b'*64,'target_id':str(uuid.uuid4()),'source_revision':'fixture',
            'socket_volume':'trading-ai_postgresql-socket','secrets_volume':'trading-ai_postgresql-secrets'}

    def write_target(self):
        import json,hashlib
        application,operators=self.release.source_hashes()
        self.target.update(application_hashes=application,operator_hashes=operators)
        path=self.release.HERE/'postgresql-target.json';path.write_text(json.dumps(self.target))
        (self.release.HERE/'automatic-acquisition-manifest.json').write_text(json.dumps({'files':{
            'ops/activation/postgresql-target.json':hashlib.sha256(path.read_bytes()).hexdigest()}}))

    def test_missing_target_preserves_legacy_selection_and_manifest_drift_fails(self):
        self.assertIsNone(self.release.target())
        self.assertEqual(self.release.configured_image('legacy'),'legacy')
        self.write_target();self.assertEqual(self.release.verify()['image'],self.target['image'])
        (self.root/'scrapper/example.py').write_text('changed\n')
        with self.assertRaisesRegex(RuntimeError,'inventory or bytes'):
            self.release.verify()

    def test_compose_credentials_are_role_scoped_and_sqlite_file_is_read_only(self):
        import json
        self.write_target()
        identity=self.root/'identity.json';identity.write_text(json.dumps({'environment':'production','authorized':True}))
        service={'environment':{},'volumes':[{'type':'volume','source':'legacy','target':'/data'}]}
        compose={'services':{'fixture':service}}
        self.release.configure(compose,service,'documents',identity)
        self.assertTrue(service['volumes'][0]['read_only'])
        credentials=[m for m in service['volumes'] if m['target']=='/run/postgresql-credentials'][0]
        self.assertEqual(credentials['volume']['subpath'],'roles/documents')
        self.assertTrue(credentials['read_only'])
        self.assertNotIn('password',service['environment']['DATABASE_URL'])
        self.assertEqual(json.loads(identity.read_text())['postgresql']['role'],'trading_ai_documents')
        command=['docker',*self.release.reader_prefix(),'/host/acquisition_live_reader.py','summary','{}']
        self.assertTrue(self.release.is_reader_command(command))
        for change in ('--privileged','--user=root'):
            self.assertFalse(self.release.is_reader_command(command[:2]+[change]+command[2:]))
        self.assertFalse(self.release.is_reader_command([part.replace('roles/reader','roles/financial') for part in command]))
        health=['docker',*self.release.reader_prefix(),'/host/postgresql_health.py','--probe']
        self.assertTrue(self.release.is_reader_command(health))
        self.assertFalse(self.release.is_reader_command(health+['--execute']))
        self.assertFalse(self.release.is_reader_command(health[:-1]))

    def test_document_companion_uses_its_own_native_login_identity_and_passfile(self):
        import json
        from paired_downloads import document_environment
        self.write_target()
        (self.root/'identity.json').write_text(json.dumps({'environment':'production','authorized':True}))
        service={'environment':self.release.environment('financial'),'volumes':[]}
        self.release.configure_document_companion({},service,self.root)
        parent={**service['environment'],'TRADING_AI_RUNTIME_IDENTITY_FILE':'/selected/identity.json'}
        child=document_environment(parent)
        self.assertIn('trading_ai_documents@',child['DATABASE_URL'])
        self.assertEqual(child['PGPASSFILE'],'/run/postgresql-documents-credentials/pgpass')
        self.assertEqual(child['TRADING_AI_RUNTIME_IDENTITY_FILE'],'/selected/postgresql-documents-identity.json')
        self.assertIn('trading_ai_financial@',parent['DATABASE_URL'])
        self.assertEqual(json.loads((self.root/'postgresql-documents-identity.json').read_text())['postgresql']['role'],'trading_ai_documents')
        with self.assertRaisesRegex(RuntimeError,'target is missing'):
            document_environment({'TRADING_AI_DB_BACKEND':'postgresql'})

    def test_native_host_runtime_is_explicit_and_manifest_bound(self):
        import json,hashlib
        self.assertEqual(self.release.configured_host_runtime('/legacy/python','/legacy/site'),('/legacy/python','/legacy/site'))
        self.write_target()
        path=self.release.HERE/'postgresql-target.json'
        value=json.loads(path.read_text())
        value.update(host_python='/persistent/bin/python',host_site_packages='/persistent/lib/site-packages')
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,'manifest-bound'):
            self.release.configured_host_runtime('old','old')
        (self.release.HERE/'automatic-acquisition-manifest.json').write_text(json.dumps({'files':{
            'ops/activation/postgresql-target.json':hashlib.sha256(path.read_bytes()).hexdigest()}}))
        self.assertEqual(self.release.configured_host_runtime('old','old'),('/persistent/bin/python','/persistent/lib/site-packages'))
        value['host_python']='relative/python';path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,'host runtime'):
            self.release.configured_host_runtime('old','old')

    def test_release_preparation_uses_reviewed_code_and_keeps_workspace_state(self):
        from acquisition_release import prepare_release,workspace_root
        workspace=(self.root/'workspace').resolve();source=(self.root/'reviewed').resolve()
        for root in (workspace,source):
            for directory in ('scrapper','ops/activation','ops/host'):(root/directory).mkdir(parents=True)
        for directory in ('artifacts','docs','ops/docker','.git','experiments'):(workspace/directory).mkdir(parents=True,exist_ok=True)
        (workspace/'.screener_session').write_text('fixture')
        for name in ('.scheduler-admission.lock','.nse-owner.lock','.monitor-readers.lock'):
            (workspace/'ops/activation'/name).touch()
        (workspace/'scrapper/code.py').write_text('editable workspace bytes')
        (source/'scrapper/code.py').write_text('reviewed source bytes')
        release=workspace/'artifacts/acquisition-releases/native'
        prepare_release(workspace,release,source=source)
        self.assertEqual((release/'scrapper/code.py').read_text(),'reviewed source bytes')
        self.assertEqual((workspace/'scrapper/code.py').read_text(),'editable workspace bytes')
        self.assertEqual(workspace_root(release),workspace)
        self.assertEqual((release/'ops/activation/.nse-owner.lock').stat().st_ino,
                         (workspace/'ops/activation/.nse-owner.lock').stat().st_ino)
        self.assertTrue((release/'.screener_session').is_symlink())
        self.assertEqual((release/'experiments').resolve(),workspace/'experiments')
        old_style=workspace/'artifacts/acquisition-releases/default'
        prepare_release(workspace,old_style)
        self.assertEqual((old_style/'scrapper/code.py').read_text(),'editable workspace bytes')



class LocalBackupControlTests(unittest.TestCase):
    def test_dump_client_is_read_only_restricted_and_uniquely_owned(self):
        import postgresql_backup as backup
        target={'socket_volume':'socket','secrets_volume':'secrets','database':'trading_ai',
            'roles':{'backup':'trading_ai_backup'},'schema':'trading_ai','postgresql_image':'pinned-postgres'}
        command=backup.dump_command(target,'trading-ai-postgresql-backup-'+'a'*32)
        self.assertIn('--network=none',command)
        self.assertIn('--read-only',command)
        self.assertIn('PGUSER=trading_ai_backup',command)
        self.assertTrue(any('volume-subpath=roles/backup,readonly' in part for part in command))
        self.assertFalse(any('/var/lib/postgresql/data' in part for part in command))
        with self.assertRaises(ValueError):backup.dump_command(target,'trading-ai-postgresql')

    def test_interrupted_backup_records_failure_and_stops_only_its_dump_client(self):
        from unittest.mock import patch,Mock
        from types import SimpleNamespace
        import json
        import postgresql_backup as backup
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);destination=root/'backups';destination.mkdir()
            target={'target_id':'fixture','socket_volume':'socket','secrets_volume':'secrets','database':'trading_ai',
                'roles':{'backup':'trading_ai_backup'},'schema':'trading_ai','postgresql_image':'pinned-postgres'}
            dump=Mock();dump.stdout=Mock();dump.wait.return_value=0
            encrypted=Mock();encrypted.wait.side_effect=[KeyboardInterrupt(),0];encrypted.poll.return_value=None
            with patch.object(backup,'load_configuration',return_value=(target,{'scope':'local_only_owner_selected','age_binary':'age','recipient':'fixture'},destination)), \
                 patch('acquisition_release.workspace_root',return_value=root), \
                 patch.object(backup.subprocess,'Popen',side_effect=[dump,encrypted]), \
                 patch.object(backup.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as cleanup:
                with self.assertRaises(KeyboardInterrupt):backup.backup()
            receipt=json.loads((root/'artifacts/postgresql-backups/status.json').read_text())
            self.assertEqual(receipt['status'],'FAIL')
            self.assertEqual(receipt['error_type'],'KeyboardInterrupt')
            command=cleanup.call_args.args[0]
            self.assertEqual(command[:4],['docker','stop','--time','10'])
            self.assertTrue(command[4].startswith('trading-ai-postgresql-backup-'))
            self.assertNotEqual(command[4],'trading-ai-postgresql')
            encrypted.kill.assert_called_once()

    def test_first_process_start_interruption_still_stops_the_owned_container(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import json
        import postgresql_backup as backup
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);destination=root/'backups';destination.mkdir()
            target={'target_id':'fixture','socket_volume':'socket','secrets_volume':'secrets','database':'trading_ai',
                'roles':{'backup':'trading_ai_backup'},'schema':'trading_ai','postgresql_image':'pinned-postgres'}
            with patch.object(backup,'load_configuration',return_value=(target,{'scope':'local_only_owner_selected'},destination)), \
                 patch('acquisition_release.workspace_root',return_value=root), \
                 patch.object(backup.subprocess,'Popen',side_effect=KeyboardInterrupt()), \
                 patch.object(backup.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as cleanup:
                with self.assertRaises(KeyboardInterrupt):backup.backup()
            receipt=json.loads((root/'artifacts/postgresql-backups/status.json').read_text())
            self.assertEqual(receipt['status'],'FAIL')
            self.assertTrue(receipt['cleanup_verified'])
            self.assertEqual(cleanup.call_args.args[0][-1],receipt['owned_dump_container'])



class PhysicalStorageSealTests(unittest.TestCase):
    def test_native_preflight_keeps_underlying_marker_and_mount_guards(self):
        import hashlib
        from unittest.mock import patch
        import postgresql_runtime as runtime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/'.trading_ai_runtime_identity.json';marker.write_bytes(b'fixed synthetic storage identity')
            mounts=root/'mountinfo';mounts.write_text(f'1 0 0:1 / {root} ro - tmpfs tmpfs ro\n')
            baseline={'physical_markers':{'/data/.trading_ai_runtime_identity.json':{'sha256':hashlib.sha256(marker.read_bytes()).hexdigest()}}}
            with patch.object(runtime,'PHYSICAL_ROOT',root),patch.object(runtime,'MOUNTINFO',mounts):
                runtime.verify_physical_storage(baseline)
                marker.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError,'marker changed'):runtime.verify_physical_storage(baseline)
                mounts.write_text(f'1 0 0:1 / {root} rw - tmpfs tmpfs rw\n')
                with self.assertRaisesRegex(RuntimeError,'Read-only physical'):runtime.verify_physical_storage(baseline)


class LocalHealthControlTests(unittest.TestCase):
    def test_backup_health_rejects_stale_foreign_missing_or_changed_archives(self):
        from datetime import datetime,timedelta,timezone
        from postgresql_health import backup_health
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=root/'fixture.dump.age';archive.write_bytes(b'encrypted fixture')
            receipt={'status':'PASS','target_id':'selected','finished_at_utc':now.isoformat(),
                'archive':str(archive),'encrypted_bytes':archive.stat().st_size,'encrypted_sha256':'a'*64}
            self.assertEqual(backup_health(receipt,'selected',now,root)['status'],'PASS')
            for change in ({'target_id':'foreign'},{'status':'FAIL'},
                    {'finished_at_utc':(now-timedelta(hours=27)).isoformat()},
                    {'finished_at_utc':(now+timedelta(minutes=6)).isoformat()},
                    {'encrypted_bytes':1},{'archive':str(root/'missing.dump.age')},
                    {'archive':str(root.parent/'fixture.dump.age')},{'encrypted_sha256':'invalid'}):
                with self.subTest(change=change),self.assertRaises(RuntimeError):
                    backup_health({**receipt,**change},'selected',now,root)


class ActiveReaderProfileTests(unittest.TestCase):
    setUp=NativeReleaseWiringTests.setUp
    write_target=NativeReleaseWiringTests.write_target
    def test_workspace_observer_recognizes_only_pinned_active_native_reader(self):
        import acquisition_release,hashlib,json
        from unittest.mock import patch
        self.write_target()
        release_root=self.root
        command=['docker',*self.release.reader_prefix(),'/host/acquisition_live_reader.py','quarter_monitor','{"quarter":"2026-09"}']
        workspace=release_root/'workspace';(workspace/'ops/activation').mkdir(parents=True)
        with patch.multiple(self.release,ROOT=workspace,HERE=workspace/'ops/activation'),patch.object(acquisition_release,'active_root',return_value=release_root):
            self.assertTrue(self.release.is_reader_command(command))
            bad=list(command);bad[bad.index('--network=none')]='--network=bridge'
            self.assertFalse(self.release.is_reader_command(bad))
            target=release_root/'ops/activation/postgresql-target.json'
            target.write_text(target.read_text()+' ')
            with self.assertRaisesRegex(RuntimeError,'manifest-bound'):self.release.is_reader_command(command)


class NativeFinancialPriorityTests(DisposablePostgreSQLTestCase):
    def test_screen_refresh_and_cooldown_use_native_json_and_preserve_config(self):
        import json
        import financial_screen_refresh as refresh
        from ops.activation.test_financial_screen_refresh import body, Client, NOW
        with tempfile.TemporaryDirectory() as directory, self.session() as conn:
            clients=[]
            def factory(c, **kwargs):
                client=Client(c,responses=[body(1),body(2)],**kwargs)
                clients.append(client)
                return client
            result=refresh.refresh_if_due(conn,now_utc=NOW,client_factory=factory,
                snapshot_root=Path(directory)/'snapshots')
            self.assertEqual(result['enqueue']['new'],2)
            config=json.loads(conn.execute('SELECT config_json FROM scrape_runs WHERE code_version=?',
                (refresh.RUN_VERSION,)).fetchone()[0])
            self.assertIs(config['capture_complete'],True)
            self.assertEqual(config['period'],'2026-09')
            self.assertEqual(config['max_pages'],refresh.MAX_PAGES)
            self.assertEqual(refresh.refresh_if_due(conn,now_utc=NOW,client_factory=factory)['status'],'not_due')
            self.assertEqual(len(clients),1)

    def test_priority_and_revision_events_match_sqlite_without_sqlite_json_functions(self):
        import financial_priority as priority
        from scrapper.tests.test_e01_exchange_universe import ExchangeCase,nse_results
        fixture=ExchangeCase();fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture.build_universe()
        line='ALPHA,Alpha Industries Limited,Capital Goods,Quarterly,30-SEP-2026,Un-Audited,Non-cumulative,Consolidated,05-Oct-2026 18:30:00'
        fixture.discover('nse_financial_results',nse_results((line,)),'2026-10-06',result_period='2026-09')
        from scrapper.db.database import connect as sqlite_connect
        with closing(sqlite_connect(fixture.db)) as source:
            with source:
                source.execute("UPDATE screener_company_queue SET next_attempt_at_utc=NULL")
                row=source.execute('SELECT queue_item_id,exchange_filing_id FROM screener_company_queue LIMIT 1').fetchone()
                evidence='{"reason":"source_filing_refresh","filing_id":'+str(row['exchange_filing_id'])+'}'
                source.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status,evidence_json) VALUES(?,'2026-10-11T00:00:00+00:00','pending',?)",(row['queue_item_id'],evidence))
            snapshot=fixture.db.parent/'native-snapshot.db'
            report=export_snapshot(fixture.db,snapshot)
            transfer(self.settings,snapshot,self.schema,expected_sha256=report['snapshot_sha256'])
            with self.session() as native:
                kwargs=dict(now_utc='2026-10-11T00:00:00+00:00',activated_at='2026-10-01T00:00:00+00:00')
                expected=priority.select(source,**kwargs)
                actual=priority.select(native,**kwargs)
                self.assertEqual(actual,expected)
                self.assertTrue(priority.unaccepted_refresh(native,row['queue_item_id'],row['exchange_filing_id']))
                native.execute("INSERT INTO screener_company_queue_events(queue_item_id,event_at_utc,to_status,evidence_json) VALUES(?,'2026-10-11T00:00:01+00:00','accepted','{}')",(row['queue_item_id'],))
                self.assertFalse(priority.unaccepted_refresh(native,row['queue_item_id'],row['exchange_filing_id']))


class ReaderCutoverTests(unittest.TestCase):
    setUp=NativeReleaseWiringTests.setUp
    write_target=NativeReleaseWiringTests.write_target

    def test_immediately_previous_sealed_reader_can_finish_after_cutover(self):
        import shutil,json,hashlib
        self.write_target()
        previous=self.root/'artifacts/acquisition-releases/previous'
        shutil.copytree(self.release.HERE,previous/'ops/activation')
        (previous/'.acquisition-release.json').write_text(json.dumps({'workspace_root':str(self.root)}))
        old=['docker',*self.release.reader_prefix(previous),'/host/acquisition_live_reader.py','quarter_monitor','{}']
        self.target['image']='sha256:'+'c'*64
        self.write_target()
        manifest=self.release.HERE/'automatic-acquisition-manifest.json'
        value=json.loads(manifest.read_text());value['previous_release']=str(previous);manifest.write_text(json.dumps(value))
        self.assertTrue(self.release.is_reader_command(old))
        bad=list(old);bad[bad.index('--network=none')]='--network=bridge'
        self.assertFalse(self.release.is_reader_command(bad))
        target=previous/'ops/activation/postgresql-target.json'
        content=json.loads(target.read_text());content['target_id']='00000000-0000-0000-0000-000000000000';target.write_text(json.dumps(content))
        self.assertFalse(self.release.is_reader_command(old))
