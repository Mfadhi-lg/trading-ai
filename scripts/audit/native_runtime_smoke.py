#!/usr/bin/env python3
"""Required native operational coverage: a missing prerequisite/skip is failure."""
import os
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

MODULES = (
    'scrapper.tests.test_postgresql_operations.NativeObservationTests',
    'scrapper.tests.test_postgresql_operations.NativeFinancialPriorityTests',
    'scrapper.tests.test_native_operational_readers',
)


def run():
    if os.environ.get('TRADING_AI_ENV') != 'test' or not os.environ.get('TRADING_AI_POSTGRESQL_TEST_DSN'):
        print('FAIL: requires test environment and explicitly disposable PostgreSQL test DSN', file=sys.stderr)
        return 1
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print('FAIL: install the optional native PostgreSQL dependencies', file=sys.stderr)
        return 1
    for name in ('pg_dump', 'pg_restore'):
        if not shutil.which(name):
            print(f'FAIL: missing native prerequisite {name}', file=sys.stderr)
            return 1
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped or not result.testsRun:
        print('FAIL: required native smoke coverage was skipped or empty', file=sys.stderr)
        return 1
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(run())
