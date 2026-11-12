"""Keep the public-checksum exception bound to one frozen source line."""
import hashlib
import json
from pathlib import Path
import re
import tomllib
import unittest

ROOT=Path(__file__).resolve().parents[2]

class MigrationChecksumSecretScanTests(unittest.TestCase):
    def test_exception_matches_only_the_verified_public_checksum_and_profile(self):
        config=tomllib.loads((ROOT/'.gitleaks.toml').read_text())
        self.assertIs(config['extend']['useDefault'],True)
        rules={rule['id']:rule for rule in config['rules']}
        self.assertIn('screener-credential-file',rules)
        credential_rule=rules['screener-credential-file']
        self.assertIsNotNone(re.search(credential_rule['path'],'.screener_session'))
        self.assertIsNotNone(re.search(credential_rule['regex'],'session_fixture_value_123456'))
        exception=next(item for item in config['allowlists'] if item['description']=='Verified frozen SQLite 068 source checksum')
        prior=next(item for item in config['allowlists'] if item['description']=='Verified SQLite migration 068 source checksum')
        self.assertEqual(prior['paths'],[r'^scrapper/db/postgresql_migrations/manifest\.json$'])
        self.assertEqual(prior['condition'],'AND')
        self.assertEqual(exception['condition'],'AND')
        self.assertEqual(exception['targetRules'],['generic-api-key'])
        self.assertEqual(exception['regexTarget'],'match')
        name='068_exchange_source_access_compatibility.sql'
        checksum=hashlib.sha256((ROOT/'scrapper/db/migrations'/name).read_bytes()).hexdigest()
        profile=json.loads((ROOT/'scrapper/db/postgresql_migrations/sqlite_077_profile.json').read_text())
        self.assertEqual(profile['sqlite_sources'][name],checksum)
        path=re.compile(exception['paths'][0]);line=re.compile(exception['regexes'][0])
        self.assertIsNotNone(path.fullmatch('scrapper/db/postgresql_migrations/sqlite_077_profile.json'))
        self.assertIsNone(path.fullmatch('other/sqlite_077_profile.json'))
        self.assertIsNotNone(line.fullmatch(name+'": "'+checksum+'"'))
        self.assertIsNone(line.fullmatch('service_access_token": "'+checksum+'"'))
        self.assertIsNone(line.fullmatch(name+'": "'+'a'*64+'"'))
