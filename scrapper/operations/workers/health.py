"""Read-only role health. Lease renewal is never evidence of job progress."""
import math
from ...db.database import check_schema_compatibility

ROLE_COLUMNS = {
    'generic': {'artifact_download_queue': ('download_job_id', 'status', 'attempt_count', 'claim_token', 'lease_owner'),
                'artifact_download_host_health': ('host', 'blocked_until_utc')},
    'rating': {'credit_rating_download_queue': ('download_job_id', 'status', 'attempt_count', 'claim_token', 'lease_owner'),
               'credit_rating_host_policy': ('host', 'min_delay_seconds')},
    'browser': {'browser_rating_recovery_jobs': ('browser_job_id', 'run_id', 'status', 'attempt_count', 'claim_token', 'lease_owner'),
                'browser_rating_recovery_hosts': ('run_id', 'target_host', 'blocked_until_utc')},
}
WAITING = frozenset(('READY', 'IDLE', 'WAITING', 'COOLDOWN', 'THROTTLED'))


def role_health(conn, role, snapshot, *, now, dependencies, heartbeat_limit=30, progress_limit=1800):
    if role not in ROLE_COLUMNS:
        raise ValueError('Unknown worker role')
    if any(not math.isfinite(v) or v <= 0 for v in (heartbeat_limit, progress_limit)):
        raise ValueError('Health limits must be finite and positive')
    errors = []
    try:
        check_schema_compatibility(conn)
        for table, columns in ROLE_COLUMNS[role].items():
            conn.execute('SELECT ' + ','.join(columns) + ' FROM ' + table + ' LIMIT 0')
    except Exception as exc:
        errors.append('schema:' + type(exc).__name__)
    if not dependencies:
        errors.append('dependencies:unverified')
    for name, probe in dependencies.items():
        try:
            if not probe():
                errors.append('dependency:' + name)
        except Exception as exc:
            errors.append('dependency:' + name + ':' + type(exc).__name__)
    state = snapshot['state']
    heartbeat_age = max(0, now - snapshot['heartbeat_at_monotonic'])
    progress_age = max(0, now - snapshot.get('claimed_at_monotonic', snapshot['progress_at_monotonic']))
    if heartbeat_age > heartbeat_limit:
        errors.append('heartbeat:stale')
    if snapshot['active_job'] is not None and progress_age > progress_limit:
        errors.append('progress:stalled')
    if state in ('STARTING', 'STOPPED'):
        errors.append('role:' + state.lower())
    if snapshot.get('observer_error'):
        errors.append('observer:failed')
    return {'role': role, 'healthy': not errors, 'errors': errors,
            'waiting': snapshot['active_job'] is None and state in WAITING,
            'heartbeat_age_seconds': heartbeat_age, 'active_age_seconds': progress_age if snapshot['active_job'] is not None else None,
            'completed': snapshot['completed']}


def bind_role_health(lifecycle, conn, role, *, dependencies, progress_limit=1800):
    lifecycle.health_probe = lambda snapshot: role_health(conn, role, snapshot,
        now=lifecycle.clock(), dependencies=dependencies, progress_limit=progress_limit)
    lifecycle.beat('READY')
    result = lifecycle.snapshot()['health']
    if not result['healthy']:
        raise RuntimeError('Worker role health failed: ' + ', '.join(result['errors']))
