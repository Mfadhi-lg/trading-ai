"""Live, read-only visibility into the operator-directed rating backfill."""
import argparse
import sqlite3
import time
from ..config import DB_PATH


def fetch_rating_data(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='browser_rating_recovery_jobs'").fetchone()
    if not exists:
        return {'hosts': [], 'workers': [], 'current': [], 'recent': []}
    def rows(sql):
        return [dict(row) for row in conn.execute(sql)]
    hosts = rows('''SELECT job.target_host,sum(job.status='SUCCEEDED') saved,
        sum(job.status='PENDING') pending,sum(job.status='RUNNING') active,
        sum(job.status='FAILED' AND coalesce(json_extract(job.evidence_json,'$.resolution'),'')
            !='PUBLISHER_SOURCE_UNAVAILABLE') failed,
        sum(job.status='REVIEW_REQUIRED') review,
        sum(job.status='FAILED' AND json_extract(job.evidence_json,'$.resolution')
            ='PUBLISHER_SOURCE_UNAVAILABLE') unavailable,
        sum(job.status='SUCCEEDED' AND julianday(content.created_at_utc)>julianday('now','-15 minutes')) recent,
        max(CASE WHEN job.status='SUCCEEDED' THEN content.created_at_utc END) last_saved
        FROM browser_rating_recovery_jobs job LEFT JOIN artifact_contents content USING(content_id)
        GROUP BY job.target_host ORDER BY job.target_host''')
    health_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='browser_rating_recovery_workers'").fetchone()
    workers = rows('''SELECT worker_id,status,heartbeat_at_utc,
        (julianday('now')-julianday(heartbeat_at_utc))*86400 heartbeat_age
        FROM browser_rating_recovery_workers ORDER BY heartbeat_at_utc DESC''') if health_exists else []
    cooldowns = rows('''SELECT target_host,blocked_until_utc FROM browser_rating_recovery_hosts
        WHERE julianday(blocked_until_utc)>julianday('now')''')
    waits = {r['target_host']: r['blocked_until_utc'] for r in cooldowns}
    for host in hosts:
        host['cooldown'] = waits.get(host['target_host'])
    return {'hosts': hosts, 'workers': workers,
        'current': rows('''SELECT company_name,target_host,updated_at_utc,lease_expires_at_utc
            FROM browser_rating_recovery_jobs WHERE status='RUNNING' '''),
        'recent': rows('''SELECT company_name,target_host,updated_at_utc,status,error
            FROM browser_rating_recovery_jobs WHERE status IN ('SUCCEEDED','FAILED','REVIEW_REQUIRED')
            ORDER BY updated_at_utc DESC LIMIT 5''')}


def render_rating_panel(data):
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    table = Table(expand=True, box=None)
    for title in ('Publisher', 'Saved', 'Pending', 'Active', 'Failed / Review', 'Missing', 'Saved / 15m', 'Last saved UTC / Cooldown'):
        table.add_column(title)
    for host in data['hosts']:
        table.add_row(host['target_host'], str(host['saved']), str(host['pending']), str(host['active']),
            f"{host['failed']} / {host['review']}", str(host['unavailable'] or 0), str(host['recent']),
            f"Waiting until {host['cooldown']}" if host['cooldown'] else host['last_saved'] or '-')
    lines = []
    for worker in data['workers']:
        status = worker['status']
        if status != 'STOPPED' and worker['heartbeat_age'] > 180:
            status = 'STALE — check process'
        lines.append(Text(f"{worker['worker_id']}: {status} (last activity {max(0, int(worker['heartbeat_age']))}s ago)"))
    if not lines:
        lines.append(Text('No worker heartbeats yet; an active lease alone does not prove a worker is running.'))
    for current in data['current']:
        lines.append(Text(f"Current: {current['company_name']} · {current['target_host']} · since {current['updated_at_utc']}"))
    recent = Table(title='Latest rating download outcomes', box=None, expand=True)
    for title in ('Company', 'Publisher', 'Outcome', 'Time UTC / Error'):
        recent.add_column(title)
    for row in data['recent']:
        recent.add_row(row['company_name'], row['target_host'], row['status'], row['error'][:100] if row['error'] else row['updated_at_utc'])
    return Panel(Group(table, *lines, recent), title='Rating downloads · ICRA / CRISIL / other agencies',
        subtitle='Saved = file imported; Missing = publisher file unavailable. Ctrl+C returns to CLI.', border_style='cyan')


def run_rating_monitor(conn):
    from rich.console import Console
    from rich.live import Live
    try:
        with Live(render_rating_panel(fetch_rating_data(conn)), console=Console(), refresh_per_second=1) as live:
            while True:
                time.sleep(2)
                live.update(render_rating_panel(fetch_rating_data(conn)))
    except KeyboardInterrupt:
        pass


def main():
    from rich.console import Console
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    conn = sqlite3.connect(f'file:{DB_PATH.resolve()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        if args.live and not args.once:
            run_rating_monitor(conn)
        else:
            Console().print(render_rating_panel(fetch_rating_data(conn)))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
