"""Offline ownership evidence regressions; no process termination or live DB."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

spec=importlib.util.spec_from_file_location('production_ownership',Path(__file__).with_name('production_ownership.py'))
gate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class OwnershipTests(unittest.TestCase):
    def process(self,args,exe='node'):
        return {'pid':123,'executable':exe,'args':args}

    def test_chatgpt_workspace_argument_without_db_handles_is_not_owner(self):
        p=self.process('/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node --experimental-vm-modules /tmp/trusted-worker.js /Users/user/Fadhi/trading-ai')
        handles=[{'name':'/Users/user/Fadhi/trading-ai','fd':'cwd'}, {'name':'/dev/null','access':'u'}]
        self.assertEqual(gate.classify_process(p,handles)[0],'unrelated')

    def test_codex_and_frontend_context_is_not_acquisition(self):
        for args in ('codex /Users/user/trading-ai','node vite.js --root /Users/user/trading-ai','node /tmp/browser.js screener.in'):
            self.assertEqual(gate.classify_process(self.process(args))[0],'unrelated')

    def test_node_and_codex_writable_production_handles_are_never_exempt(self):
        for exe in ('node','codex','/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node'):
            for access in ('w','u'):
                handle={'name':'/data/scraper.db-wal','access':access}
                self.assertEqual(gate.classify_process(self.process(exe,exe),[handle])[0],'conflict')

    def test_unknown_production_handle_access_is_ambiguous(self):
        self.assertEqual(gate.classify_process(self.process('node any.js'),[{'name':'/data/scraper.db'}])[0],'ambiguous')

    def test_readonly_production_reader_is_not_writer(self):
        self.assertEqual(gate.classify_process(self.process('python read.py'),[{'name':'/data/scraper.db','access':'r'}])[0],'unrelated')

    def test_kernel_ownership_lock_blocks_even_readonly_descriptor(self):
        f={'name':'/data/scraper.db.exchange-executor.lock','access':'r','lock':'W'}
        self.assertEqual(gate.classify_process(self.process('python loop.py'),[f])[0],'conflict')

    def test_production_acquisition_between_db_opens_still_blocks(self):
        p=self.process('python -m scrapper.cli exchange-scheduler --serve','python')
        self.assertEqual(gate.classify_process(p,environment={'TRADING_AI_ENV':'production'})[0],'conflict')

    def test_duplicate_operator_and_unknown_acquisition_scope_block(self):
        for args in ('python ops/activation/bse_retained.py --execute','python -m scrapper.cli exchange-scheduler'):
            self.assertEqual(gate.classify_process(self.process(args))[0],'ambiguous')

    def test_explicit_isolated_development_worker_is_allowed(self):
        p=self.process('python -m scrapper.cli screener-company-worker')
        env={'TRADING_AI_ENV':'development','SCRAPER_DB_PATH':'/tmp/dev/scraper.db'}
        self.assertEqual(gate.classify_process(p,environment=env)[0],'unrelated')
        self.assertEqual(gate.classify_process(p,[{'name':'/data/scraper.db','access':'u'}],env)[0],'conflict')

    def container(self,mounts,args=(),state='running'):
        return {'State':{'Status':state},'Mounts':mounts,'Path':'python','Args':list(args)}

    def test_writable_production_mount_blocks_any_container_role(self):
        for mount in ({'Name':gate.VOLUME,'RW':True}, {'Name':gate.VOLUME},
                      {'Source':'/var/lib/docker/volumes/'+gate.VOLUME+'/_data','RW':True}):
            self.assertIsNotNone(gate.container_conflict(self.container([mount],['node','dev.js'])))

    def test_readonly_reader_and_unrelated_development_container_do_not_block(self):
        self.assertIsNone(gate.container_conflict(self.container([{'Name':gate.VOLUME,'RW':False}],['read.py'])))
        self.assertIsNone(gate.container_conflict(self.container([{'Name':'dev-data','RW':True}],['exchange-scheduler'])))
        self.assertIsNone(gate.container_conflict(self.container([{'Name':gate.VOLUME,'RW':True}],state='exited')))

    def test_readonly_duplicate_acquisition_owner_still_blocks(self):
        self.assertIsNotNone(gate.container_conflict(self.container([{'Name':gate.VOLUME,'RW':False}],['/app/bse_retained_runtime.py'])))

    def test_lsof_access_and_lock_evidence_is_parsed(self):
        found=gate.parse_lsof('p123\ncnode\nf7\nau\nlW\nn/data/scraper.db\n')
        self.assertEqual(found[123][0],{'fd':'7','access':'u','lock':'W','name':'/data/scraper.db'})

    def lease_db(self,state,expiry):
        c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE exchange_coordinator_tasks(state TEXT,owner TEXT,lease_until_utc TEXT)')
        c.execute('INSERT INTO exchange_coordinator_tasks VALUES (?,?,?)',(state,'owner',expiry))
        return c

    def test_unexpired_claim_conflict_is_detected_without_mutating(self):
        c=self.lease_db('RUNNING','2026-10-09T12:00:00+00:00')
        changes=c.total_changes
        with self.assertRaisesRegex(RuntimeError,'Unexpired'):
            gate.assert_no_conflicting_leases(c,now='2026-10-09T06:00:00+00:00')
        self.assertEqual(c.total_changes,changes)
        c.close()

    def test_expired_and_released_leases_do_not_become_false_owners(self):
        for state,expiry in (('RUNNING','2026-10-08T12:00:00+00:00'),('RELEASED','2026-10-10T12:00:00+00:00')):
            c=self.lease_db(state,expiry)
            gate.assert_no_conflicting_leases(c,now='2026-10-09T06:00:00+00:00'); c.close()

    def test_unresolved_lease_timestamp_stops(self):
        c=self.lease_db('RUNNING','unknown')
        with self.assertRaisesRegex(RuntimeError,'Ambiguous'):
            gate.assert_no_conflicting_leases(c,now='2026-10-09T06:00:00+00:00')
        c.close()


if __name__=='__main__':
    unittest.main()
