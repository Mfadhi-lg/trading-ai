"""Read-only reasons current-quarter financial rows are not claimable."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1] / 'activation'),
                str(Path(__file__).resolve().parents[2]), '/activation', '/app']
from scrapper.sources.exchange.reporting_periods import priority_result_periods
from financial_diagnostic_reader import observation


def inspect(conn, now, period=None):
    period = period or priority_result_periods(now=datetime.fromisoformat(now))[0]
    rows=[dict(row) for row in conn.execute('''SELECT status,
      coalesce(collection_disposition,'none') disposition,
      CASE WHEN collection_disposition IS NOT NULL THEN 'collection_disposition'
        WHEN EXISTS (SELECT 1 FROM screener_scope_aliases a
          JOIN screener_collection_scope s ON s.company_path=a.scope_path
          WHERE a.company_path=q.company_path_normalized AND s.eligibility='below_floor') THEN 'below_floor_alias'
        WHEN next_attempt_at_utc>? THEN 'future_due_time'
        ELSE 'due' END reason,
      count(*) n,min(next_attempt_at_utc) earliest_due,max(next_attempt_at_utc) latest_due
      FROM screener_company_queue q
      WHERE result_period=? AND status IN ('pending','failed_retryable')
      GROUP BY 1,2,3''',(now,period))]
    samples=[dict(row) for row in conn.execute('''SELECT company_path_normalized,
      next_attempt_at_utc,collection_disposition,last_error_json
      FROM screener_company_queue WHERE result_period=?
      AND status IN ('pending','failed_retryable') ORDER BY queue_item_id LIMIT 6''',(period,))]

    return {'current_quarter_pending':rows,'samples':samples,'observed_at_utc':now,'period':period}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite-snapshot', type=Path, help='Explicit frozen SQLite input; never current native status')
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with observation(args.sqlite_snapshot or Path('/data/scraper.db'), sqlite_snapshot=args.sqlite_snapshot is not None) as (conn, source):
            result = inspect(conn, now)
            result.update(status='verified', database_source=source)
    except Exception:
        print(json.dumps({'status':'unknown','observed_at_utc':now,'detail':'Financial observation unavailable; no fallback performed.'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
