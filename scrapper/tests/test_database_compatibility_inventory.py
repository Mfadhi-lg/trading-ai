"""Verify the static database audit against canonical migration source bytes."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / 'docs/database/migration-inventory.json'
DECLARATION = re.compile(r'^\s*(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|VIEW|INDEX|TRIGGER)|ALTER\s+TABLE|DROP\s+(?:TABLE|VIEW|INDEX|TRIGGER))\b', re.I)


class DatabaseCompatibilityInventoryTests(unittest.TestCase):
    def test_inventory_covers_canonical_migrations_and_exact_source(self):
        inventory = json.loads(INVENTORY.read_text())
        self.assertEqual(inventory['source_manifest_sha256'], hashlib.sha256((ROOT / inventory['source_manifest']).read_bytes()).hexdigest())
        entries = inventory['migrations']
        actual = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'scrapper/db/migrations').glob('*.sql'))
        self.assertEqual([e['path'] for e in entries], actual)
        self.assertEqual(len(entries), inventory['migration_count'])
        declaration_count = 0
        for entry in entries:
            with self.subTest(path=entry['path']):
                raw = (ROOT / entry['path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
                lines = raw.decode().splitlines()
                self.assertEqual(len(lines), entry['lines'])
                declarations = [{'line': i, 'text': line.strip()} for i, line in enumerate(lines, 1) if DECLARATION.match(line)]
                self.assertEqual(declarations, entry['declarations'])
                declaration_count += len(declarations)
        self.assertEqual(declaration_count, inventory['declaration_entry_count'])
        self.assertEqual(declaration_count, sum(inventory['declaration_counts'].values()))
