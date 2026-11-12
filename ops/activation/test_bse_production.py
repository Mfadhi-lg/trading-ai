"""Offline production config selection; no Docker, browser or database access."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import bse_production as config


def legacy(tmp_path, value=None):
    value = value or {'operation_image': 'sha256:' + 'a' * 64,
                      'application_hashes': {'scrapper/example.py': 'b' * 64}}
    path = tmp_path / 'bse-production.json'
    path.write_text(json.dumps(value))
    (tmp_path / 'automatic-acquisition-manifest.json').write_text(json.dumps({
        'files': {'ops/activation/bse-production.json': hashlib.sha256(path.read_bytes()).hexdigest()}}))
    return value


def test_sealed_legacy_config_needs_no_artifacts(tmp_path):
    value = legacy(tmp_path)
    with patch.object(config, 'HERE', tmp_path):
        assert config.production_config() == value


def test_config_drift_is_rejected(tmp_path):
    legacy(tmp_path)
    (tmp_path / 'bse-production.json').write_text('{}')
    with patch.object(config, 'HERE', tmp_path), pytest.raises(RuntimeError, match='manifest-bound'):
        config.production_config()


def test_invalid_inventory_is_rejected_even_when_hash_bound(tmp_path):
    legacy(tmp_path, {'operation_image': 'sha256:' + 'a' * 64,
                     'application_hashes': {'scrapper/../../outside.py': 'b' * 64}})
    with patch.object(config, 'HERE', tmp_path), pytest.raises(RuntimeError, match='inventory'):
        config.production_config()


def test_native_config_is_verified_without_legacy_manifest(tmp_path):
    (tmp_path / 'automatic-acquisition-manifest.json').write_text('{"files": {}}')
    (tmp_path / 'postgresql-target.json').write_text('{}')
    native = {'image': 'sha256:' + 'c' * 64,
              'application_hashes': {'scrapper/example.py': 'd' * 64}}
    with patch.object(config, 'HERE', tmp_path), patch.dict(sys.modules, {
            'postgresql_release': SimpleNamespace(verify=lambda: native)}):
        assert config.production_config() == {
            'operation_image': native['image'], 'application_hashes': native['application_hashes']}


def test_native_verification_failure_never_falls_back(tmp_path):
    legacy(tmp_path)
    (tmp_path / 'postgresql-target.json').write_text('{}')
    def reject():
        raise RuntimeError('Native release configuration drift')
    with patch.object(config, 'HERE', tmp_path), patch.dict(sys.modules, {
            'postgresql_release': SimpleNamespace(verify=reject)}), pytest.raises(RuntimeError, match='drift'):
        config.production_config()


def test_missing_declared_native_target_never_uses_legacy(tmp_path):
    legacy(tmp_path)
    (tmp_path / 'automatic-acquisition-manifest.json').write_text(json.dumps({
        'files': {'ops/activation/postgresql-target.json': 'e' * 64}}))
    with patch.object(config, 'HERE', tmp_path), pytest.raises(RuntimeError, match='target is missing'):
        config.production_config()


def test_scheduled_adapter_uses_production_config():
    source = Path(__file__).with_name('automatic_acquisition.py').read_text()
    assert 'manifest = production_config()' in source
    assert 'bse-production-ingestion-ownership-fixed' not in source
