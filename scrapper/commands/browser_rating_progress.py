"""Read-only status and exceptions for a finite browser rating recovery run."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone

from ..config import DB_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='browser-ratings-20261001')
    parser.add_argument('--include-exceptions', action='store_true')
    args = parser.parse_args()
    conn = sqlite3.connect(f'file:{DB_PATH.resolve()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    def rows(sql):
        return [dict(r) for r in conn.execute(sql, (args.run_id,))]
    counts = rows('''SELECT target_host,status,count(*) jobs
        FROM browser_rating_recovery_jobs WHERE run_id=?
        GROUP BY target_host,status ORDER BY target_host,status''')
    payload = {
        'run_id': args.run_id, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'total_jobs': sum(r['jobs'] for r in counts),
        'imported_reports': sum(r['jobs'] for r in counts if r['status'] == 'SUCCEEDED'),
        'counts': counts,
        'downloads_last_15_minutes': rows('''SELECT target_host,count(*) reports
            FROM browser_rating_recovery_jobs WHERE run_id=? AND status='SUCCEEDED'
            AND julianday(updated_at_utc)>julianday('now','-15 minutes') GROUP BY target_host'''),
        'current_downloads': rows('''SELECT browser_job_id,company_name,target_host,
            updated_at_utc AS started_at_utc FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status='RUNNING' '''),
        'active_host_cooldowns': rows('''SELECT * FROM browser_rating_recovery_hosts
            WHERE run_id=? AND julianday(blocked_until_utc)>julianday('now')'''),
        'host_cooldowns': rows('SELECT * FROM browser_rating_recovery_hosts WHERE run_id=?'),
        'processing': rows('''SELECT coalesce(json_extract(evidence_json,'$.processing_status'),'PENDING') status,
            count(*) jobs FROM browser_rating_recovery_jobs WHERE run_id=? AND status='SUCCEEDED'
            GROUP BY coalesce(json_extract(evidence_json,'$.processing_status'),'PENDING')'''),
        'normalizations': rows('''SELECT n.normalization_status,count(*) reports,sum(n.accepted_action_count) accepted_rating_actions
            FROM browser_rating_recovery_jobs job JOIN credit_rating_normalizations n
              ON n.normalization_id=json_extract(job.evidence_json,'$.normalization_id')
            WHERE job.run_id=? GROUP BY n.normalization_status'''),
    }
    if args.include_exceptions:
        payload['exceptions'] = rows('''SELECT browser_job_id,artifact_id,company_name,
            target_host,requested_url,status,error FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status IN ('FAILED','REVIEW_REQUIRED') ORDER BY browser_job_id''')
    print(json.dumps(payload, indent=2, sort_keys=True))
    conn.rollback()
    conn.close()


if __name__ == '__main__':
    main()
