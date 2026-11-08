"""Research overlay contracts on invented source rows in temporary databases."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scrapper.research.subsector_ss01 import importer
from scrapper.research.subsector_ss03 import build as ss03, pilot
from scrapper.research.subsector_ss04 import build as ss04


class PortableSubsectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'synthetic.sqlite3'
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        c = self.conn
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('CREATE TABLE companies(company_id INTEGER PRIMARY KEY,current_legal_name TEXT,status TEXT)')
        c.executemany('INSERT INTO companies VALUES(?,?,?)', [(1,'Synthetic Alpha','active'),(2,'Synthetic Beta','active'),(3,'Synthetic Property','active'),(405,'Synthetic Wheels','active')])
        c.execute('CREATE TABLE security_identifiers(id INTEGER PRIMARY KEY,company_id INTEGER,identifier_type TEXT,identifier_value TEXT,is_current INTEGER)')
        c.executemany('INSERT INTO security_identifiers VALUES(?,?,?,?,1)', [(1,1,'NSE','SYNTHA'),(2,2,'NSE','SYNTHB'),(3,405,'NSE','SYNTHW')])
        importer.migrate(c)
        snapshot = {'tables': {'companies': {'rows': [(1,'Synthetic Alpha','active'),(2,'Synthetic Beta','active'),(3,'Synthetic Property','active'),(405,'Synthetic Wheels','active')]}, 'security_identifiers': {'rows': [(1,1,'NSE','SYNTHA'),(2,2,'NSE','SYNTHB'),(3,405,'NSE','SYNTHW')]}, 'source_identifiers': {'rows': []}}, 'bse_aliases': []}
        source_file = self.root / 'source.csv'
        source_file.write_text('Symbol,Company Name,Category\nSYNTHA,Synthetic Alpha,TYRE\nSYNTHB,Synthetic Beta,TYRE\nSYNTHW,Synthetic Wheels,AUTO ANCILLIARY\nSYNTHW,Synthetic Wheels,AUTO ANCILLARY\nSYNTHA,Synthetic Alpha,TYRE\n')
        source, rows = importer.parse_source(source_file, 'csv', 'FY2026-Q4', '2026-03-31')
        for row in rows:
            row.update(importer.resolve(row, snapshot))
        importer.import_rows(c, {'sources': [source], 'rows': rows})
        c.execute('CREATE TABLE ss02_identity_overlay(row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),company_id INTEGER REFERENCES companies(company_id),state TEXT,evidence_json TEXT)')
        c.execute('CREATE TABLE ss_focus_entries(entry_id TEXT PRIMARY KEY,display_stock_raw TEXT)')
        c.execute('INSERT INTO ss_focus_entries VALUES(?,?)', ('synthetic-entry','Synthetic Alpha'))
        c.commit()

    def test_aliases_deduplicate_memberships_and_keep_unknown_availability(self):
        before = self.conn.execute('SELECT * FROM ss_rows ORDER BY row_id').fetchall()
        ss03.migrate(self.conn)
        for label in ('AUTO ANCILLIARY','AUTO ANCILLARY'):
            self.assertEqual(self.conn.execute('SELECT a.canonical_id FROM ss03_alias a JOIN ss_subsectors s USING(subsector_id) WHERE s.label_raw=?', (label,)).fetchone()[0], ss03.cid('AUTO ANCILLARY'))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM ss03_historical_memberships WHERE canonical_id=?', (ss03.cid('TYRE'),)).fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT * FROM ss_rows ORDER BY row_id').fetchall(), before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM ss_sources WHERE known_at IS NOT NULL').fetchone()[0], 0)

    def test_ss03_migration_idempotency_rollback_and_integrity(self):
        original = ss04.fingerprint(self.conn)
        ss03.migrate(self.conn)
        rows = self.conn.execute('SELECT * FROM ss03_alias ORDER BY subsector_id').fetchall()
        ss03.migrate(self.conn)
        self.assertEqual(rows, self.conn.execute('SELECT * FROM ss03_alias ORDER BY subsector_id').fetchall())
        ss03.rollback(self.conn)
        ss03.rollback(self.conn)
        self.assertEqual(original, ss04.fingerprint(self.conn))
        ss03.migrate(self.conn)
        self.assertEqual(self.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def plan(self):
        taxonomy = [{'code': k, 'label': k, 'definition': 'Invented fixture activity', 'origin': 'synthetic fixture'} for k in ('TYRES','WHEEL_RIMS','WIND_TURBINES','PROPERTY_DEVELOPMENT')]
        mappings = []
        for cid,name,code,text in [(1,'Synthetic Alpha','TYRES','Synthetic tyre maker'),(3,'Synthetic Property','PROPERTY_DEVELOPMENT','Synthetic property company with unspecified business mix'),(405,'Synthetic Wheels','WIND_TURBINES','Manufacture of road wheels and wind turbine parts')]:
            mappings.append({'company_id': cid, 'company_name': name, 'primary_code': code, 'status': 'proposed', 'origin': 'synthetic fixture', 'known_at': '2026-05-01', 'evidence_sha256': 'a'*64, 'tutor_evidence': [], 'exposures': [{'code': code, 'role': 'primary', 'evidence': {'basis': 'invented test description'}}], 'evidence': {'display_text': text, 'available_at_utc': None}, 'official_business_evidence': []})
        return {'taxonomy': taxonomy, 'mappings': mappings, 'q2': [{'entry_id': 'synthetic-entry', 'company_id': 1}]}

    def test_ss04_no_product_part_or_broad_parent_promoted_and_sources_preserved(self):
        ss03.migrate(self.conn)
        before = ss04.fingerprint(self.conn)
        ss04.apply(self.conn, self.plan(), {1,405})
        decisions = {cid: json.loads(raw) for cid,raw in self.conn.execute('SELECT company_id,decision_json FROM ss04_decisions')}
        self.assertEqual(decisions[1]['status'], 'VERIFIED_TUTOR')
        self.assertIsNone(decisions[3]['primary_code'])
        self.assertEqual(decisions[3]['status'], 'UNRESOLVED')
        self.assertNotIn('WIND_TURBINES', {e['code'] for e in decisions[405]['exposures']})
        self.assertEqual(decisions[405]['status'], 'MULTI_ACTIVITY_REVIEW')
        self.assertTrue(all(d['valid_from'] is None for d in decisions.values()))
        self.assertEqual(before, ss04.fingerprint(self.conn))

    def test_ss04_idempotent_replay_changed_input_refusal_and_rollback(self):
        ss03.migrate(self.conn)
        before = ss04.fingerprint(self.conn)
        plan = self.plan()
        ss04.apply(self.conn, plan, {1})
        rows = self.conn.execute('SELECT * FROM ss04_decisions ORDER BY company_id').fetchall()
        ss04.apply(self.conn, plan, {1})
        self.assertEqual(rows, self.conn.execute('SELECT * FROM ss04_decisions ORDER BY company_id').fetchall())
        with self.assertRaisesRegex(ValueError, 'Frozen revision input/code mismatch'):
            ss04.apply(self.conn, plan, {2})
        ss04.rollback(self.conn)
        ss04.rollback(self.conn)
        self.assertEqual(before, ss04.fingerprint(self.conn))
        ss04.apply(self.conn, plan, {1})
        self.assertEqual(rows, self.conn.execute('SELECT * FROM ss04_decisions ORDER BY company_id').fetchall())
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_pilot_only_writes_temporary_copy_and_keeps_provisional_limits(self):
        c = self.conn
        c.execute('CREATE TABLE ss02_business_evidence(company_id INTEGER PRIMARY KEY,description_raw TEXT)')
        c.executemany('INSERT INTO ss02_business_evidence VALUES(?,?)', [(1,'Synthetic tyre maker'),(2,'Synthetic unrelated company'),(405,'Synthetic wheel maker')])
        c.commit()
        before = ss04.fingerprint(c)
        scope = self.root / 'artifacts/subsector-ss02'
        scope.mkdir(parents=True)
        (scope / 'existing-collection-scope-inventory.csv').write_text('company_id,eligibility\n1,eligible\n2,eligible\n405,eligible\n')
        output = self.root / 'pilot-output'
        target = self.root / 'pilot-copy.sqlite3'
        candidates = {'TYRE': [(1,'Invented tyre activity','synthetic://issuer','2025-01-01','Synthetic source date')]}
        with ExitStack() as stack:
            for module,name,value in [(ss03,'SOURCE',self.db),(pilot,'R',self.root),(pilot,'OUT',output),(pilot,'DB',target),(pilot,'CANDIDATES',candidates),(pilot,'DECISIONS',{2: ('EXCLUDE','Invented non-tyre activity')})]:
                stack.enter_context(patch.object(module, name, value))
            stack.enter_context(patch.object(pilot,'historical', return_value=[]))
            with redirect_stdout(io.StringIO()):
                pilot.run()
                first = (output / 'CANDIDATE_BASKETS.csv').read_bytes()
                pilot.run()
            self.assertEqual(first, (output / 'CANDIDATE_BASKETS.csv').read_bytes())
        self.assertEqual(before, ss04.fingerprint(c))
        with (output / 'CANDIDATE_BASKETS.csv').open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['company_id'], '1')
            self.assertEqual(row['eligibility'], 'PROVISIONAL')
            self.assertEqual(row['listing_continuity'], 'NOT_ESTABLISHED')
            self.assertEqual(row['full_price_coverage'], 'NOT_ESTABLISHED')
            self.assertTrue(row['historical_weight'].startswith('UNAVAILABLE'))
            self.assertLessEqual(row['source_date'], row['cutoff'])
        copied = sqlite3.connect(target)
        self.addCleanup(copied.close)
        self.assertEqual(copied.execute("SELECT decision FROM ss03_pilot_review WHERE company_id=2").fetchone()[0], 'EXCLUDE')
        self.assertEqual(copied.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_ss02_overlay_replay_preserves_original_rows_and_fences_changed_plan(self):
        from scrapper.research.subsector_ss02 import build, full_universe
        c = self.conn
        plan = self.plan()
        plan.update(baseline_sha256=build.before_hash(c), migration_sha256=importer.digest(build.MIGRATION.read_bytes()), frozen_at='2026-05-01', identity_overlays=[], q2=[], security_mapping=[], tutor_crosswalk=[], baskets=[])
        before = build.before_hash(c)
        full_universe.apply_to(c, plan)
        first = full_universe.all_counts(c)
        full_universe.apply_to(c, plan)
        self.assertEqual(first, full_universe.all_counts(c))
        self.assertEqual(before, build.before_hash(c))
        self.assertEqual(c.execute('SELECT count(*) FROM ss02_company_mapping WHERE valid_from IS NOT NULL').fetchone()[0], 0)
        changed = deepcopy(plan)
        changed['frozen_at'] = '2099-01-01'
        with self.assertRaisesRegex(ValueError, 'cannot be silently replaced'):
            full_universe.apply_to(c, changed)
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
