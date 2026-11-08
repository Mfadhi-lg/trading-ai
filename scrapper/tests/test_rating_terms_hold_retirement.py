"""Offline coverage of the retired defaults and existing-ledger upgrade."""
import sqlite3
import unittest
from pathlib import Path

from scrapper.core.access_policy import is_blocked_terms_target
from scrapper.core.host_policy import policy_for
from scrapper.db.host_permits import acquire, finish


MIGRATIONS = Path(__file__).parents[1] / 'db' / 'migrations'


class RatingTermsHoldRetirementTests(unittest.TestCase):
    def test_direct_worker_includes_reenabled_publishers(self):
        from scrapper.operations.workers.rating import INSTANCES
        self.assertTrue({'crisil.com', 'www.crisil.com', 'crisilratings.com',
                         'www.crisilratings.com', 'icra.in', 'www.icra.in'}
                        <= set(INSTANCES['other'].hosts))

    def test_fresh_schema_has_no_retired_policy_rows(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        for migration in sorted(MIGRATIONS.glob('*.sql')):
            conn.executescript(migration.read_text())
        rows = conn.execute("SELECT automation_status,max_concurrency,min_delay_seconds FROM credit_rating_host_policy WHERE host IN ('crisil.com','www.crisil.com','crisilratings.com','www.crisilratings.com','icra.in','www.icra.in')").fetchall()
        self.assertEqual(rows, [('UNSPECIFIED', 1, 4.0)] * 6)
        self.assertEqual(acquire(conn,'https://www.crisil.com/a',mode='rating',owner='test',now=100).reason,
                         'GRANTED')

    def test_default_download_and_resolver_reach_mocked_publishers(self):
        from scrapper.tests.test_d02_document_downloader import FakeSession, FakeResponse, PDF_BYTES
        from scrapper.documents.downloader import download_url
        from scrapper.documents.credit_rating_resolver import resolve_credit_rating
        for host in ('www.crisil.com', 'www.crisilratings.com', 'www.icra.in'):
            url = 'https://' + host + '/report.pdf'
            with self.subTest(host=host):
                session = FakeSession({url: FakeResponse(PDF_BYTES, url=url, content_type='application/pdf')})
                result = download_url(url, artifact_type='credit_rating', session=session)
                self.assertEqual(session.calls, [url])
                self.assertEqual(result.detected_media_type, 'application/pdf')
                session = FakeSession({url: FakeResponse(PDF_BYTES, url=url, content_type='application/pdf')})
                resolved = resolve_credit_rating(url, session=session)
                self.assertNotEqual(resolved.status, 'SKIPPED_POLICY')
                self.assertEqual(session.calls, [url])

    def test_default_hosts_and_subdomains_are_eligible(self):
        for host in ('crisil.com', 'www.crisil.com', 'crisilratings.com',
                     'assets.crisilratings.com', 'icra.in', 'www.icra.in'):
            with self.subTest(host=host):
                self.assertFalse(is_blocked_terms_target('https://' + host + '/report.pdf'))
                self.assertFalse(policy_for(host).blocked)
                self.assertEqual(policy_for(host).max_concurrency, 1)
        self.assertTrue(is_blocked_terms_target('https://blocked.example/a',
                                               additional_hosts=('blocked.example',)))

    def test_upgrade_requeues_only_retired_terms_holds_and_preserves_cooldown(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.executescript('''
            CREATE TABLE credit_rating_host_policy(host TEXT PRIMARY KEY,
                automation_status TEXT, max_concurrency INTEGER, min_delay_seconds REAL,
                policy_basis TEXT, reviewed_at_utc TEXT);
            CREATE TABLE artifact_sources(document_url TEXT, automation_status TEXT);
            CREATE TABLE credit_rating_download_queue(target_host TEXT, status TEXT,
                next_attempt_at_utc TEXT, lease_owner TEXT, lease_expires_at_utc TEXT,
                last_result_status TEXT, last_error TEXT, updated_at_utc TEXT,
                attempt_count INTEGER);
            CREATE TABLE artifact_download_queue AS SELECT * FROM credit_rating_download_queue;
        ''')
        hosts = ('crisil.com', 'www.crisil.com', 'crisilratings.com', 'www.icra.in',
                 'blocked.example', 'evilicra.in')
        conn.executemany("INSERT INTO credit_rating_host_policy VALUES(?,'BLOCKED_TERMS',0,8,'old','old')",
                         [(host,) for host in hosts])
        for table in ('credit_rating_download_queue', 'artifact_download_queue'):
            conn.executemany(f"INSERT INTO {table} VALUES(?,?,'later',NULL,NULL,?,'old','old',2)", [
                ('www.crisil.com', 'SKIPPED_POLICY', 'PUBLISHER_TERMS_BLOCK_AUTOMATION'),
                ('www.icra.in', 'SKIPPED_POLICY', 'PUBLISHER_POLICY_BLOCKED'),
                ('www.icra.in', 'SUCCEEDED', 'PDF_VALIDATED'),
                ('www.icra.in', 'SKIPPED_POLICY', 'AUTOMATIC_RECORDING_DOWNLOAD_DISABLED'),
                ('evilicra.in', 'SKIPPED_POLICY', 'PUBLISHER_TERMS_BLOCK_AUTOMATION'),
            ])
        conn.executemany('INSERT INTO artifact_sources VALUES(?,?)', [
            ('https://www.icra.in/report.pdf', 'BLOCKED_TERMS'),
            ('https://www.crisilratings.com/report.pdf', 'BLOCKED_TERMS'),
            ('https://evilicra.in/report.pdf', 'BLOCKED_TERMS'),
        ])
        conn.executescript((MIGRATIONS / '071_shared_host_permits.sql').read_text())
        conn.execute("INSERT INTO acquisition_host_state(publisher_group,policy_version,blocked_until) VALUES('icra','test',200)")
        conn.commit()
        conn.executescript((MIGRATIONS / '076_retire_crisil_icra_terms_holds.sql').read_text())
        for table in ('credit_rating_download_queue', 'artifact_download_queue'):
            rows = conn.execute(f'SELECT status,attempt_count FROM {table} ORDER BY rowid').fetchall()
            self.assertEqual(rows, [('PENDING',2), ('PENDING',2), ('SUCCEEDED',2),
                                    ('SKIPPED_POLICY',2), ('SKIPPED_POLICY',2)])
        self.assertEqual(conn.execute('SELECT automation_status FROM artifact_sources ORDER BY rowid').fetchall(),
                         [('AUTOMATION_ELIGIBILITY_UNKNOWN',), ('AUTOMATION_ELIGIBILITY_UNKNOWN',), ('BLOCKED_TERMS',)])
        self.assertEqual(acquire(conn,'www.icra.in',mode='rating',owner='test',now=100).reason, 'HOST_DEFERRED')
        grant = acquire(conn,'www.icra.in',mode='rating',owner='test',now=201)
        self.assertEqual(grant.reason, 'GRANTED')
        finish(conn,grant.permit,now=202)
        self.assertEqual(acquire(conn,'icra.in',mode='rating',owner='test',now=204).retry_at, 209)
        self.assertEqual(acquire(conn,'blocked.example',mode='rating',owner='test',now=210).reason,
                         'PUBLISHER_POLICY_BLOCKED')
