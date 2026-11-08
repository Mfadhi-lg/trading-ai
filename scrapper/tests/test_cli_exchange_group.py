"""Exchange adapter contracts: return codes, delegation and connection ownership."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scrapper import cli
from scrapper.cli_groups.exchange import COMMANDS


class ExchangeCliTests(unittest.TestCase):
    def invoke(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = cli.main(argv)
        return status, json.loads(output.getvalue())

    def test_group_has_exact_existing_commands(self):
        self.assertEqual(set(COMMANDS), {name for name in cli._parser()._subparsers._group_actions[0].choices if name.startswith('exchange-')})

    def test_discovery_and_refresh_exit_states_and_arguments(self):
        for command, handler, dataset in [('exchange-refresh-universe', 'refresh_exchange_universe', 'nse_equity_master'), ('exchange-discover-results', 'discover_exchange_results', 'bse_financial_results')]:
            for payload, expected in [({}, 0), ({'validation': {'status': 'failed'}}, 1), ({'enqueue': {'manual_review': 2}}, 1)]:
                with self.subTest(command=command, payload=payload), patch.object(cli, handler, return_value=payload) as call:
                    status, output = self.invoke([command, '--dataset', dataset, '--dry-run'])
                    self.assertEqual(status, expected)
                    self.assertEqual(output, {'status': 'ok', **payload})
                    options = call.call_args.kwargs
                    self.assertTrue(options['dry_run'])
                    self.assertEqual(options['dataset'], dataset)
                    self.assertEqual('result_period' in options, command == 'exchange-discover-results')
            for exc in (OSError('offline'), ValueError('invalid'), RuntimeError('refused')):
                with patch.object(cli, handler, side_effect=exc):
                    self.assertEqual(self.invoke([command, '--dataset', dataset]), (2, {'status': 'error', 'error': str(exc)}))

    def test_inspection_delegates_and_reports_errors(self):
        with patch.object(cli, 'inspect_exchange_dataset', return_value={'rows': 3}) as call:
            self.assertEqual(self.invoke(['exchange-inspect-dataset', '--dataset', 'nse_equity_master', '--sample-rows', '3']), (0, {'status': 'ok', 'rows': 3}))
            self.assertEqual(call.call_args.kwargs['sample_rows'], 3)
        with patch.object(cli, 'inspect_exchange_dataset', side_effect=ValueError('bad fixture')):
            self.assertEqual(self.invoke(['exchange-inspect-dataset', '--dataset', 'nse_equity_master'])[0], 2)

    def test_scheduler_modes_arguments_and_return_codes(self):
        cases = [([], 'run_scheduler'), (['--once'], 'run_scheduler'), (['--status'], 'day_status'), (['--weekly-report'], 'run_weekly_report'), (['--catch-up', '--catch-up-days', '3'], 'catch_up_missed_days')]
        for flags, owner in cases:
            for payload, expected in [({}, 0), ({'status': 'ok'}, 0), ({'status': 'held'}, 1)]:
                with self.subTest(flags=flags, payload=payload), patch.object(cli, owner, return_value=payload) as call:
                    self.assertEqual(self.invoke(['exchange-scheduler', *flags]), (expected, {'status': 'ok', **payload}))
                    self.assertEqual(call.call_args.kwargs['db_path'], cli.DB_PATH)
                    if owner == 'catch_up_missed_days': self.assertEqual(call.call_args.kwargs['max_days'], 3)
        with patch.object(cli, 'run_scheduler', side_effect=ImportError('optional unavailable')):
            self.assertEqual(self.invoke(['exchange-scheduler'])[0], 2)
        self.assertEqual(self.invoke(['exchange-scheduler', '--resume-evidence', 'reviewed'])[0], 2)
        self.assertEqual(self.invoke(['exchange-scheduler', '--resume-access', 'bse_security_master', '--once'])[0], 2)
        with self.assertRaisesRegex(NameError, "name 'resume_source_access' is not defined"):
            self.invoke(['exchange-scheduler', '--resume-access', 'bse_security_master'])
        # Retain the facade patch path for operators supplying this legacy hook.
        with patch.object(cli, 'resume_source_access', create=True, return_value={}) as call:
            self.assertEqual(self.invoke(['exchange-scheduler', '--resume-access', 'bse_security_master', '--resume-evidence', 'reviewed'])[0], 0)
            call.assert_called_once_with('bse_security_master', 'reviewed', db_path=cli.DB_PATH)

    def test_read_only_reports_exit_states_close_connections_and_never_migrate(self):
        cases = [('exchange-reconciliation-report', 'write_exchange_reconciliation_report', [{'reconciliation_status': 'complete', 'summary': {'open_conflict_count': 0}}, {'reconciliation_status': 'review', 'summary': {'open_conflict_count': 1}}]), ('exchange-universe-coverage', 'write_exchange_universe_coverage', [{'coverage_status': state, 'missing_required_datasets': [], 'summary': {}} for state in ('complete', 'incomplete')])]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'
            for command, owner, payloads in cases:
                for expected, payload in enumerate(payloads):
                    output.write_text(json.dumps(payload))
                    conn = Mock()
                    with patch.object(cli, 'connect', return_value=conn) as connect, patch.object(cli, 'init_db') as init, patch.object(cli, owner, return_value=output):
                        status, result = self.invoke([command, '--output', str(output)])
                        self.assertEqual(status, expected)
                        self.assertEqual(result['output'], str(output.resolve()))
                        connect.assert_called_once_with(cli.DB_PATH, read_only=True)
                        init.assert_called_once_with(conn)
                        conn.close.assert_called_once_with()
                conn = Mock()
                with patch.object(cli, 'connect', return_value=conn), patch.object(cli, 'init_db'), patch.object(cli, owner, side_effect=RuntimeError('schema refused')):
                    self.assertEqual(self.invoke([command, '--output', str(output)])[0], 2)
                    conn.close.assert_called_once_with()

    def test_conflict_resolution_uses_same_clock_writer_and_close(self):
        conn = Mock()
        with patch.object(cli, 'connect', return_value=conn) as connect, patch.object(cli, 'init_db') as init, patch.object(cli, 'utc_now', return_value='fixed-clock'), patch.object(cli, 'resolve_identity_conflict', return_value={'resolved': True}) as resolve:
            status, _ = self.invoke(['exchange-resolve-conflict', '--conflict-id', '7', '--resolution', 'dismissed'])
            self.assertEqual(status, 0)
            connect.assert_called_once_with(cli.DB_PATH)
            init.assert_called_once_with(conn)
            resolve.assert_called_once_with(conn, conflict_id=7, resolution='dismissed', now_utc='fixed-clock')
            conn.close.assert_called_once_with()

    def test_bulk_and_unaccepted_preserve_old_command_owners(self):
        for command, module, attr in [('exchange-bulk-enqueue', 'scrapper.commands.exchange_bulk_enqueue', 'run_bulk_enqueue'), ('exchange-unaccepted-backfill', 'scrapper.commands.exchange_unaccepted_backfill', 'run_unaccepted_backfill')]:
            conn = Mock()
            with patch.object(cli, 'connect', return_value=conn), patch.object(cli, 'init_db') as init, patch(module+'.'+attr, return_value={'queued': 2}) as owner:
                self.assertEqual(self.invoke([command, '--dry-run']), (0, {'status': 'ok', 'queued': 2}))
                owner.assert_called_once_with(conn, dry_run=True)
                init.assert_called_once_with(conn)
                conn.close.assert_called_once_with()
            conn = Mock()
            with patch.object(cli, 'connect', return_value=conn), patch.object(cli, 'init_db'), patch(module+'.'+attr, side_effect=RuntimeError('blocked')):
                self.assertEqual(self.invoke([command])[0], 2)
                conn.close.assert_called_once_with()

    def test_period_backfill_retains_shared_screener_route_and_exit_states(self):
        for flags, repair in [(['--dry-run'], False), ([], True)]:
            for payload, expected in [({'missing_count': 0, 'refresh_failures': 0}, 0), ({'missing_count': 1, 'refresh_failures': 0}, 1), ({'missing_count': 0, 'refresh_failures': 1}, 1)]:
                with patch('scrapper.commands.screener_period_coverage.run_period_coverage', return_value=payload) as owner, patch.object(cli, 'connect') as connect:
                    self.assertEqual(self.invoke(['exchange-unaccepted-backfill', '--result-period', '2026-06-30', '--limit', '2', *flags]), (expected, payload))
                    owner.assert_called_once_with(period='2026-06-30', symbols=(), repair=repair, refresh=repair, limit=2, db_path=cli.DB_PATH)
                    connect.assert_not_called()
        with patch('scrapper.commands.screener_period_coverage.run_period_coverage', return_value={'missing_count': 0, 'refresh_failures': 0}) as owner:
            self.assertEqual(self.invoke(['screener-period-coverage', '--period', '2026-06-30'])[0], 0)
            self.assertEqual(owner.call_args.kwargs['period'], '2026-06-30')
        with patch('scrapper.commands.screener_period_coverage.run_period_coverage', side_effect=ValueError('same exception')):
            with self.assertRaisesRegex(ValueError, 'same exception'):
                self.invoke(['exchange-unaccepted-backfill', '--result-period', '2026-06-30'])

    def test_output_identity_guard_precedes_exchange_dispatch(self):
        with patch.object(cli, 'assert_configured_writable_targets', side_effect=RuntimeError('production output refused')), patch.object(cli, 'write_exchange_universe_coverage') as owner:
            with self.assertRaisesRegex(RuntimeError, 'production output refused'):
                cli.main(['exchange-universe-coverage', '--output', '/tmp/refused.json'])
            owner.assert_not_called()
