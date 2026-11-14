"""Offline fresh-result handoff and measurement contracts in isolated stores."""
import json
import tempfile
import unittest
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from scrapper.db.database import connect, init_db
from scrapper.db.documents.expectations import ensure_result_artifact_expectations
from scrapper.db.documents.freshness import acquisition_freshness_report, register_filing_document_expectations
from scrapper.documents.availability import DEFAULT_RETRY_OFFSETS_HOURS, RetryPolicy
from scrapper.documents.freshness import download_timings, measured_delays, result_retry_policy
from scrapper.sources.exchange.result_filings import parse_result_filings

NOW = '2026-11-12T10:00:00+00:00'
BROADCAST = '2026-11-12T09:55:00+00:00'


class FreshResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = connect(Path(self.temp.name) / 'isolated.sqlite3')
        self.addCleanup(self.conn.close)
        init_db(self.conn, apply_migrations=True)
        with self.conn:
            self.cid = self.conn.execute("INSERT INTO companies(current_legal_name,status) VALUES ('Synthetic Issuer','active')").lastrowid
            self.conn.execute("INSERT INTO security_identifiers(company_id,identifier_type,identifier_value,is_current) VALUES (?,'NSE','SYNTH',1)", (self.cid,))
            self.conn.execute("""INSERT INTO screener_collection_scope(company_path,company_id,eligibility,
                floor_crore,observed_at_utc,evidence_json,next_check_at_utc)
                VALUES ('/company/SYNTH/',?,'eligible','580',?,'{}',?)""", (self.cid, NOW, NOW))
        self.filing = dict(filing_id=123, exchange='NSE', exchange_key='SYNTH', result_period='2026-09',
                           period_end_date='2026-09-30', broadcast_at_utc=BROADCAST)
        self.outcome = dict(decision='queued', company_path_normalized='/company/SYNTH/', path_basis='nse_symbol')

    def ensure(self, **options):
        return ensure_result_artifact_expectations(self.conn, company_id=self.cid, filing_id=123,
            exchange='NSE', exchange_key='SYNTH', result_period='2026-09', period_end='2026-09-30',
            broadcast_at_utc=BROADCAST, created_at_utc=NOW, **options)

    def test_filing_starts_due_expectations_before_any_financial_parse(self):
        ids = register_filing_document_expectations(self.conn, self.filing, self.outcome, now_utc=NOW)
        self.assertEqual(len(ids), 4)
        row = self.conn.execute("SELECT * FROM artifact_expectations WHERE artifact_type='transcript'").fetchone()
        self.assertLess(row['next_check_at_utc'], NOW)
        self.assertEqual(row['availability_status'], 'expected_not_yet_published')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM accepted_page_versions').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM artifact_download_queue').fetchone()[0], 0)
        self.assertEqual(ids, self.ensure())

    def test_old_policy_and_long_tail_are_retained_on_repeat(self):
        ids = self.ensure(policy=RetryPolicy())
        row = self.conn.execute('SELECT schedule_json FROM artifact_expectations WHERE expectation_id=?', (ids[0],)).fetchone()
        schedule = json.loads(row[0]); schedule['long_tail_v1'] = True
        schedule['offsets_hours'].extend([2160, 4320])
        with self.conn:
            self.conn.execute('UPDATE artifact_expectations SET schedule_json=? WHERE expectation_id=?', (json.dumps(schedule), ids[0]))
        before = [tuple(row) for row in self.conn.execute('SELECT * FROM artifact_expectations')]
        self.assertEqual(self.ensure(), ids)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute('SELECT * FROM artifact_expectations')])
        with self.assertRaisesRegex(ValueError, 'persisted retry policy'):
            self.ensure(policy=RetryPolicy(offsets_hours=(1, 2)))

    def test_unknown_and_ineligible_company_identity_does_not_start_discovery(self):
        self.assertEqual(register_filing_document_expectations(self.conn, self.filing,
            dict(self.outcome, company_path_normalized='/company/UNKNOWN/'), now_utc=NOW), ())
        with self.conn:
            self.conn.execute("DELETE FROM screener_collection_scope WHERE company_id=?", (self.cid,))
        self.assertEqual(register_filing_document_expectations(self.conn, self.filing, self.outcome, now_utc=NOW), ())

    def test_report_has_unknown_latency_without_publication_evidence(self):
        with self.conn:
            self.conn.execute("""INSERT INTO document_artifacts(company_id,artifact_key,artifact_type,
                title_raw,first_observed_at_utc) VALUES (?,'synthetic','transcript','Synthetic',?)""", (self.cid, NOW))
        report = acquisition_freshness_report(self.conn, since_utc=BROADCAST)
        self.assertEqual(report['link_discovery']['status'], 'UNKNOWN')
        self.assertEqual(report['link_discovery']['unknown_samples'], 1)
        self.assertFalse(report['live_target_verified'])

    def test_status_command_only_reads_explicit_quiescent_snapshot(self):
        snapshot = Path(self.temp.name) / 'snapshot.sqlite3'
        with sqlite3.connect(snapshot) as target:
            self.conn.backup(target)
        command = [sys.executable, '-B', str(Path(__file__).parents[2] / 'scripts/document-freshness'),
                   '--snapshot', str(snapshot), '--since', BROADCAST]
        before = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['filing_detection']['status'], 'UNKNOWN')
        self.assertEqual(before, hashlib.sha256(snapshot.read_bytes()).hexdigest())
        Path(str(snapshot) + '-wal').write_bytes(b'isolated test sidecar')
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('quiescent snapshot', refused.stderr)


class TimingPolicyTests(unittest.TestCase):
    def test_revision_retaining_original_broadcast_has_distinct_identity(self):
        header = 'SYMBOL,COMPANY NAME,QUARTER END DATE,AUDITED / UNAUDITED,CONSOLIDATED / STANDALONE,BROADCAST DATE/TIME,REVISED DATE/TIME\n'
        original = header + 'SYNTH,Synthetic Issuer,30-SEP-2026,Un-Audited,Consolidated,12-Nov-2026 15:25:00,\n'
        revised = header + 'SYNTH,Synthetic Issuer,30-SEP-2026,Un-Audited,Consolidated,12-Nov-2026 15:25:00,12-Nov-2026 15:30:00\n'
        first = parse_result_filings(original.encode(), 'nse_financial_results').filings[0]
        second = parse_result_filings(revised.encode(), 'nse_financial_results').filings[0]
        self.assertNotEqual(first.filing_key, second.filing_key)
        self.assertEqual(second.broadcast_at_utc, NOW)
        self.assertEqual(second.raw_row['BROADCAST DATE/TIME'], '12-Nov-2026 15:25:00')

    def test_accelerated_policy_is_finite_and_retains_long_tail(self):
        policy = result_retry_policy(broadcast_at_utc=BROADCAST, observed_at_utc=NOW)
        self.assertEqual(policy.check_at(BROADCAST, 0), '2026-11-12T09:56:00+00:00')
        self.assertEqual(policy.offsets_hours[4:], DEFAULT_RETRY_OFFSETS_HOURS)
        self.assertEqual(policy.unknown_retry_limit, 7)
        next_check, _ = policy.next_after_result(expected_after_utc=BROADCAST, completed_attempt_count=1,
            status='expected_not_yet_published', retryable=True, checked_at_utc='2026-11-12T12:00:00+00:00')
        self.assertEqual(next_check, '2026-11-12T12:14:00+00:00')
        historical = result_retry_policy(broadcast_at_utc=BROADCAST, observed_at_utc='2026-11-14T10:00:00+00:00')
        self.assertEqual(historical.offsets_hours, DEFAULT_RETRY_OFFSETS_HOURS)

    def test_measured_delays_do_not_claim_target_for_unknown_or_future_times(self):
        rows = [{'start': BROADCAST, 'end': NOW}, {'start': None, 'end': NOW}, {'start': NOW, 'end': BROADCAST}]
        result = measured_delays(rows, earlier='start', later='end', target_seconds=900)
        self.assertEqual((result['samples'], result['unknown_samples'], result['within_target']), (1, 2, 1))
        self.assertEqual(result['maximum_seconds'], 300)

    def test_download_rate_uses_actual_attempt_time_including_failures(self):
        result = download_timings([dict(result_status='validated', elapsed_ms=1000, byte_size=100),
                                  dict(result_status='timed_out', elapsed_ms=1000, byte_size=None)])
        self.assertEqual(result['validated_bytes_per_request_second'], 50)
        self.assertEqual(download_timings([])['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
