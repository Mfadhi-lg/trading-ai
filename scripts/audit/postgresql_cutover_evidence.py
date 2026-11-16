#!/usr/bin/env python3
"""Check retained transfer report consistency offline; never connect to a store.

PASS means historical report consistency only. Snapshot custody, cutover watermark,
post-cutover additions and current runtime health require separate evidence.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scrapper.db.postgresql_schema import manifest, source_profile


def summary_valid(value):
    return (isinstance(value, dict) and type(value.get('rows')) is int
            and value['rows'] >= 0 and isinstance(value.get('sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None)


def assess(export, imported, *, profile='077'):
    """Compare every reviewed table, including empty tables and SQLite ledger."""
    if profile not in ('076', '077'):
        raise ValueError('Unsupported source profile')
    expected = set((source_profile() if profile == '077' else manifest())['objects']['table'])
    tables = {name: 'UNKNOWN' for name in sorted(expected)}
    result = dict(scope='historical_report_consistency_only', status='UNKNOWN',
                  profile=profile, tables=tables, reasons=[], production_health='UNKNOWN')
    if export is None or imported is None:
        result['reasons'].append('missing_transfer_report')
        return result
    if not isinstance(export, dict) or not isinstance(imported, dict):
        result.update(status='FAIL', reasons=['invalid_report'])
        return result
    digest = export.get('snapshot_sha256')
    if (type(export.get('format')) is not int or export['format'] != 1
            or type(imported.get('format')) is not int or imported['format'] != 1
            or export.get('status') != 'verified_snapshot'
            or imported.get('status') not in ('committed', 'verified_existing')
            or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
            or imported.get('snapshot_sha256') != digest):
        result.update(status='FAIL', reasons=['invalid_status_format_or_snapshot_binding'])
        return result
    source = export.get('tables')
    target = imported.get('tables')
    if not isinstance(source, dict) or not isinstance(target, dict):
        result.update(status='FAIL', reasons=['invalid_table_inventory'])
        return result
    if (set(source) | set(target)) - expected:
        result['reasons'].append('unexpected_tables')
    for name in tables:
        if name not in source or name not in target:
            continue
        original, copied = source[name], target[name]
        if (summary_valid(original) and isinstance(copied, dict)
                and copied.get('verified') is True
                and summary_valid(copied.get('source'))
                and summary_valid(copied.get('destination'))
                and original == copied['source'] == copied['destination']):
            tables[name] = 'PASS'
        else:
            tables[name] = 'FAIL'
    if 'FAIL' in tables.values() or result['reasons']:
        result['status'] = 'FAIL'
    elif 'UNKNOWN' in tables.values():
        result['reasons'].append('missing_table_evidence')
    else:
        result['status'] = 'PASS'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-report', type=Path)
    parser.add_argument('--import-report', type=Path)
    parser.add_argument('--profile', choices=('076', '077'), default='077')
    args = parser.parse_args(argv)
    try:
        reports = [json.loads(path.read_text()) if path else None
                   for path in (args.export_report, args.import_report)]
        result = assess(*reports, profile=args.profile)
    except (OSError, ValueError, UnicodeError):
        # Neither private paths nor malformed source/exception text is emitted.
        result = dict(status='FAIL', scope='historical_report_consistency_only',
                      reasons=['unreadable_or_invalid_report'], production_health='UNKNOWN')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
