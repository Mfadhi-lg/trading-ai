"""Offline entry-point proofs: never launch Docker or open retained databases."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ACTIVATION = ROOT / 'ops/activation'
sys.path.insert(0, str(ACTIVATION))
import legacy_sqlite_guard as guard

WRAPPERS = [ACTIVATION / (name + '.py') for name in (
    'step3_inspect', 'step3_canary_state', 'phase3_inspect', 'phase4_inspect',
    'phase4_attempt02_verify', 'phase4_attempt03_verify', 'phase4_canary_verify',
    'phase4_trial_preflight', 'phase4_trial_observer', 'phase4_followup',
)] + [ROOT / 'ops/benchmarks/run_phase4_throughput.py',
      ROOT / 'ops/docker/canary/ar013_readonly_host.py']
RUNTIMES = [ACTIVATION / (name + '.py') for name in (
    'step3_inspect_runtime', 'step3_detail_runtime', 'step3_accepted_details_runtime',
    'step3_canary_state_runtime', 'phase3_inspect_runtime', 'phase4_inspect_runtime',
    'phase4_attempt02_verify_runtime', 'phase4_attempt03_verify_runtime',
    'phase4_canary_verify_runtime', 'phase4_fingerprint_preflight_runtime',
    'phase4_retry_preflight_runtime', 'phase4_routine_preflight_runtime',
    'phase4_trial_snapshot_runtime', 'phase4_gap_inspect_runtime',
    'phase4_followup_snapshot_runtime', 'acquisition_recovery_backup_runtime',
    'acquisition_recovery_inspect_runtime', 'acquisition_recovery_integrity_benchmark',
    'acquisition_recovery_processing_state',
)] + [ROOT / 'ops/host/phase3_local_benchmark.py']


class LegacyLauncherTests(unittest.TestCase):
    def test_host_entrypoints_reject_before_any_campaign_dependency_or_docker(self):
        for path in WRAPPERS:
            with self.subTest(path=path.name), patch('subprocess.run') as docker:
                with self.assertRaisesRegex(RuntimeError, 'snapshot-only launcher retired'):
                    runpy.run_path(str(path), run_name='__main__')
                docker.assert_not_called()

    def test_direct_runtimes_reject_native_before_imports_or_sqlite(self):
        for path in RUNTIMES:
            with self.subTest(path=path.name), patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'postgresql'}), patch('sqlite3.connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'refused for native configuration'):
                    runpy.run_path(str(path), run_name='__main__')
                connect.assert_not_called()

    def test_benchmark_and_canary_callable_entrypoints_reject_native(self):
        for relative in ('ops/benchmarks/phase4_throughput.py', 'ops/docker/canary/ar013_readonly_db.py'):
            namespace = runpy.run_path(str(ROOT / relative))
            with self.subTest(path=relative), patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'postgresql'}), patch('sqlite3.connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'refused for native configuration'):
                    namespace['main']()
                connect.assert_not_called()

    def test_retained_bse_refuses_before_campaign_reads_writes_or_connections(self):
        for filename, calls in (
            ('bse_retained.py', (('main', ()),)),
            ('bse_retained_runtime.py', (('run', ()), ('_run_owned', (None,)))),
        ):
            namespace = runpy.run_path(str(ACTIVATION / filename))
            for backend in ('sqlite', 'postgresql'):
                for name, args in calls:
                    with self.subTest(file=filename, backend=backend, entry=name), \
                            patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': backend}), \
                            patch.object(sys, 'argv', [filename, '--execute']), \
                            patch.object(Path, 'read_text') as read, \
                            patch.object(Path, 'mkdir') as mkdir, \
                            patch('subprocess.check_output') as docker, \
                            patch('sqlite3.connect') as connect:
                        with self.assertRaisesRegex(RuntimeError, 'Retained BSE ingestion retired'):
                            namespace[name](*args)
                        read.assert_not_called()
                        mkdir.assert_not_called()
                        docker.assert_not_called()
                        connect.assert_not_called()

    def test_retained_bse_direct_entrypoints_refuse_without_stop_receipt(self):
        for filename in ('bse_retained.py', 'bse_retained_runtime.py'):
            with self.subTest(file=filename), patch.object(Path, 'mkdir') as mkdir, \
                    patch.object(Path, 'write_text') as write, \
                    patch('sqlite3.connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'Retained BSE ingestion retired'):
                    runpy.run_path(str(ACTIVATION / filename), run_name='__main__')
                mkdir.assert_not_called()
                write.assert_not_called()
                connect.assert_not_called()

    def test_release_and_missing_declared_target_reject_without_native_env(self):
        for active, declared in ((False, False), (True, False), (True, True)):
            with self.subTest(active=active, declared=declared), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
                root = Path(tmp); selected = root / 'release' if active else root
                activation = selected / 'ops/activation'; activation.mkdir(parents=True)
                if declared:
                    (activation / 'automatic-acquisition-manifest.json').write_text(json.dumps({'files': {'ops/activation/postgresql-target.json': 'missing'}}))
                else:
                    (activation / 'postgresql-target.json').write_text('{}')
                with patch('acquisition_release.active_root', return_value=selected):
                    with self.assertRaisesRegex(RuntimeError, 'refused for native release'):
                        guard.reject_native(root)

    def test_snapshot_requires_explicit_inputs_before_reading_source(self):
        with patch.object(guard, 'reject_native'), patch.dict(os.environ, {}, clear=True), patch.object(Path, 'open') as source:
            with self.assertRaisesRegex(RuntimeError, 'explicit test input and SHA256 required'):
                guard.require_snapshot()
            source.assert_not_called()

    def test_snapshot_hash_and_readonly_mount_enforced(self):
        body = b'explicit retained source bytes'
        env = {'TRADING_AI_ENV': 'test', 'TRADING_AI_HISTORICAL_SQLITE_INPUT': '/data/scraper.db',
               'TRADING_AI_HISTORICAL_SQLITE_SHA256': hashlib.sha256(body).hexdigest()}
        from io import BytesIO
        for mount, digest, expected in (
            ('rw', env['TRADING_AI_HISTORICAL_SQLITE_SHA256'], 'mounted read-only'),
            ('ro', '0' * 64, 'SHA256 mismatch'),
            ('ro', env['TRADING_AI_HISTORICAL_SQLITE_SHA256'], None),
        ):
            with self.subTest(mount=mount, digest=digest), patch.object(guard, 'reject_native'), patch.dict(os.environ, {**env, 'TRADING_AI_HISTORICAL_SQLITE_SHA256': digest}, clear=True), patch.object(Path, 'read_text', return_value=f'1 0 0:1 / /data {mount} - ext4 source {mount}'), patch.object(Path, 'is_symlink', return_value=False), patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'open', return_value=BytesIO(body)):
                if expected:
                    with self.assertRaisesRegex(RuntimeError, expected):guard.require_snapshot()
                else:guard.require_snapshot()

    def test_writable_child_mount_shadows_readonly_parent(self):
        env = {'TRADING_AI_ENV': 'test', 'TRADING_AI_HISTORICAL_SQLITE_INPUT': '/data/scraper.db',
               'TRADING_AI_HISTORICAL_SQLITE_SHA256': '0' * 64}
        mounts = '1 0 0:1 / /data ro - ext4 source ro\n2 1 0:2 / /data/scraper.db rw - ext4 source rw'
        with patch.object(guard, 'reject_native'), patch.dict(os.environ, env, clear=True), patch.object(Path, 'read_text', return_value=mounts), patch.object(Path, 'open') as source:
            with self.assertRaisesRegex(RuntimeError, 'mounted read-only'):
                guard.require_snapshot()
            source.assert_not_called()

    def test_imported_followup_launchers_refuse_before_campaign_effects(self):
        import phase4_followup
        for name in ('prepare', 'activate', 'run', 'prepare_authorized_restart'):
            with self.subTest(name=name), patch.object(Path, 'open') as opened, patch.object(Path, 'read_text') as read, patch('subprocess.run') as docker:
                with self.assertRaisesRegex(RuntimeError, 'snapshot-only launcher retired'):
                    getattr(phase4_followup, name)()
                opened.assert_not_called();read.assert_not_called();docker.assert_not_called()

    def test_native_recovery_dispatch_rejects_before_sqlite_backup_receipt(self):
        import acquisition_recovery_dispatch as dispatch
        with patch.object(sys, 'argv', ['acquisition_recovery_dispatch.py', '--execute', '--document-stage', 'processing']), patch.dict(os.environ, {'TRADING_AI_DB_BACKEND': 'postgresql'}), patch.object(Path, 'read_text') as receipt, patch.object(dispatch.scheduler, 'run_slot') as run_slot:
            with self.assertRaisesRegex(RuntimeError, 'refused for native configuration'):
                dispatch.main()
            receipt.assert_not_called()
            run_slot.assert_not_called()

    def test_root_manual_never_falls_back_to_legacy_compose(self):
        with tempfile.TemporaryDirectory() as tmp, patch('acquisition_release.active_root', return_value=Path(tmp)), patch.object(sys, 'argv', ['run_acquisition_lifecycle.py', 'manual', 'schema-status']), patch('subprocess.run') as docker:
            with self.assertRaisesRegex(RuntimeError, 'snapshot-only launcher retired'):
                runpy.run_path(str(ROOT / 'ops/host/run_acquisition_lifecycle.py'), run_name='__main__')
            docker.assert_not_called()

    def test_root_manual_preserves_native_route_and_literal_arguments(self):
        import plistlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); activation = root / 'ops/activation'; activation.mkdir(parents=True)
            (activation / 'postgresql-target.json').write_text('{}')
            config = {'Label': 'com.trading-ai.automatic-acquisition',
                      'ProgramArguments': [sys.executable, str(activation / 'automatic_acquisition.py'), '--tick'],
                      'EnvironmentVariables': {'PYTHONPATH': 'sealed-path'}}
            (activation / 'com.trading-ai.automatic-acquisition.plist').write_bytes(plistlib.dumps(config))
            literal = 'file with spaces; $(touch forbidden).csv'
            with patch('acquisition_release.active_root', return_value=root), patch.object(sys, 'argv', ['launcher', 'manual', '--file', literal]), patch('os.execve', side_effect=SystemExit(0)) as execute, patch('subprocess.run') as docker:
                with self.assertRaises(SystemExit):
                    runpy.run_path(str(ROOT / 'ops/host/run_acquisition_lifecycle.py'), run_name='__main__')
                self.assertEqual(execute.call_args.args[1], [sys.executable, '-B', str(root / 'ops/host/postgresql_cli.py'), '--file', literal])
                self.assertEqual(execute.call_args.args[2]['PYTHONPATH'], 'sealed-path')
                docker.assert_not_called()

    def test_root_continuous_alias_rejected(self):
        result = subprocess.run(['bash', str(ROOT / 'scraper'), 'run-acquisition-role'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('continuous legacy acquisition', result.stderr)

    def test_root_compose_services_cannot_start_legacy_roles(self):
        # Execute every rendered refusal entrypoint locally; no Docker invocation.
        for filename in ('compose.yml', 'compose.browser-recovery.yml'):
            source = (ROOT / filename).read_text().split('services:\n')[1].split('\nvolumes:\n')[0]
            services = [line for line in source.splitlines() if line.startswith('  ') and not line.startswith('   ') and line.endswith(':')]
            entries = [json.loads(line.split('entrypoint: ', 1)[1]) for line in source.splitlines() if line.startswith('    entrypoint: ')]
            self.assertEqual(len(entries), len(services))
            self.assertGreater(len(entries), 0)
            for entry in entries:
                result = subprocess.run(entry, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Retired SQLite production role', result.stderr)


if __name__ == '__main__':unittest.main()
