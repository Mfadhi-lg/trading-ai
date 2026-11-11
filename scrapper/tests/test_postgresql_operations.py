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
