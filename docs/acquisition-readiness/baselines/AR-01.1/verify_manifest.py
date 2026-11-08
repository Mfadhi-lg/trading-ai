#!/usr/bin/env python3
"""Fail on any schema delta beyond the reviewed seven migration effects."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
before = {item['name']: item for item in json.loads((OUT / 'schema-before.json').read_text())}
after = {item['name']: item for item in json.loads((OUT / 'schema-after.json').read_text())}
effects = json.loads((OUT / 'migration-effect-manifest.json').read_text())
pending = json.loads((OUT / 'migration-checksums.json').read_text())['pending']
assert pending == [m['filename'] for m in effects['migrations']]
assert not set(before) - set(after), 'existing schema objects removed'
modified = [name for name in before if before[name] != after[name]]
assert set(modified) == {'artifact_download_queue','credit_rating_download_queue','browser_rating_recovery_jobs'}, modified
expected_added = {name for migration in effects['migrations'] for name in migration['new_objects']}
added = set(after) - set(before)
assert expected_added <= added, expected_added - added
automatic_indexes = added - expected_added
assert all(after[name]['type'] == 'index' and after[name]['sql'] is None and
           name.startswith('sqlite_autoindex_') and after[name]['tbl_name'] in expected_added
           for name in automatic_indexes), automatic_indexes
for migration in effects['migrations']:
    for prerequisite in migration['baseline_schema_prerequisites']:
        assert prerequisite in before and before[prerequisite]['type'] == 'table'
    for new_name in migration['new_objects']:
        assert new_name not in before
    for added_column in migration['new_columns']:
        table, column = added_column.split('.')
        rows_before = json.loads((OUT / 'rows-before.json').read_text())
        assert column not in rows_before[table]['columns']
        assert 'claim_token TEXT' in after[table]['sql']
result = {'expected_modified_objects': sorted(modified), 'expected_new_objects': sorted(expected_added),
          'implicit_unique_indexes': sorted(automatic_indexes), 'unexpected_schema_deltas': [],
          'manifest_preconditions': 'PASS', 'schema_effect_review': 'PASS',
          'claim_type': 'CLONE_REHEARSAL'}
(OUT / 'schema-verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
