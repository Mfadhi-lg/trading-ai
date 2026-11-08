"""Atomic durable request reservations, independent of queue retry accounting.

Each operation owns a short BEGIN IMMEDIATE transaction. Never hold one during
HTTP/browser work. One reservation is for one outbound request, including each
redirect/subrequest. Expiry bounds reservation ownership, not external I/O;
executors must bound/cancel I/O before expiry or renew ahead of it (C20.2-4).
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from datetime import datetime
import math
import json
from dataclasses import asdict
import secrets
from ..core.host_policy import MODES, OperatorAuthorization, policy_for

@dataclass(frozen=True)
class Permit:
    token: str
    group: str
    host: str
    mode: str
    owner: str
    expires_at: float

@dataclass(frozen=True)
class Decision:
    reason: str
    permit: Permit | None = None
    retry_at: float | None = None

class PermitLost(RuntimeError):
    pass


def number(value, *, positive=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError('Expected a finite nonnegative UTC epoch/duration')
    return value

@contextmanager
def transaction(conn):
    if conn.in_transaction:
        raise ValueError('Permit operations require a connection without an outer transaction')
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def reviewed_policy(conn, target):
    policy = policy_for(target)
    # Any restriction on an alias constrains the whole explicitly shared publisher.
    # Unrelated hosts retain independent state.
    for host, status, concurrency, delay in conn.execute(
            'SELECT host,automation_status,max_concurrency,min_delay_seconds FROM credit_rating_host_policy'):
        if policy_for(host).group != policy.group:
            continue
        policy = replace(policy,
            blocked=policy.blocked or status == 'BLOCKED_TERMS',
            max_concurrency=min(policy.max_concurrency, int(concurrency)),
            min_delay_seconds=max(policy.min_delay_seconds, number(delay)))
    return policy


def inherited_cooldown(conn, group):
    # Preserve already persisted partition cooldowns when a group first participates.
    # Presence is optional only for minimal permit fixtures. No legacy row is edited.
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    until=0.0
    for table,host_column in [('artifact_download_host_health','host'),
            ('credit_rating_host_health','host'),('browser_rating_recovery_hosts','target_host')]:
        if table not in tables:
            continue
        for host,deadline in conn.execute('SELECT '+host_column+',blocked_until_utc FROM '+table+' WHERE blocked_until_utc IS NOT NULL'):
            if policy_for(host).group != group:
                continue
            dt=datetime.fromisoformat(deadline)
            if dt.tzinfo is None:
                raise ValueError('Persisted host cooldown must include UTC timezone')
            until=max(until,number(dt.timestamp()))
    return until


def state(conn, policy, now):
    conn.execute('INSERT OR IGNORE INTO acquisition_host_state(publisher_group,policy_version) VALUES(?,?)',
                 (policy.group, policy.version))
    # Backward clock correction/restart never makes a persisted deadline earlier.
    conn.execute('UPDATE acquisition_host_state SET blocked_until=max(blocked_until,?) WHERE publisher_group=?',
                 (inherited_cooldown(conn, policy.group), policy.group))
    row = conn.execute('SELECT next_request_at,blocked_until,circuit_until,observed_at,consecutive_failures FROM acquisition_host_state WHERE publisher_group=?',
                       (policy.group,)).fetchone()
    now = max(now, row[3])
    conn.execute('UPDATE acquisition_host_state SET observed_at=?,policy_version=? WHERE publisher_group=?',
                 (now, policy.version, policy.group))
    return row, now


def acquire(conn, target, *, mode, owner, now, lease_seconds=60, authorization=None):
    now, lease = number(now), number(lease_seconds, positive=True)
    if mode not in MODES or not isinstance(owner, str) or not owner.strip():
        raise ValueError('Explicit acquisition mode and nonempty owner required')
    host = policy_for(target).host
    if authorization is not None and mode != 'browser':
        raise ValueError('Operator authorization applies only to the finite browser lane')
    if mode == 'browser' and (not isinstance(authorization, OperatorAuthorization)
            or not authorization.covers(host, now)):
        return Decision('OPERATOR_AUTHORIZATION_REQUIRED')
    if authorization is not None:
        number(authorization.expires_at, positive=True)
    with transaction(conn):
        policy = reviewed_policy(conn, target)
        # Existing finite operator lane is separate from normal automation.
        # A boolean or unrelated-host receipt cannot override reviewed restrictions.
        restricted = mode == 'browser' and host in authorization.restricted_hosts
        if (policy.blocked or policy.max_concurrency == 0) and not restricted:
            return Decision('PUBLISHER_POLICY_BLOCKED')
        if restricted:
            policy = replace(policy, max_concurrency=1)
        if mode == 'browser' and not authorization.covers(host, max(now, conn.execute(
                'SELECT COALESCE(MAX(observed_at),0) FROM acquisition_host_state WHERE publisher_group=?',
                (policy.group,)).fetchone()[0])):
            return Decision('OPERATOR_AUTHORIZATION_REQUIRED')
        row, now = state(conn, policy, now)
        retry_at = max(row[:3])
        if retry_at > now:
            return Decision('HOST_DEFERRED', retry_at=retry_at)
        active = conn.execute("SELECT lease_expires_at FROM acquisition_host_permits WHERE publisher_group=? AND status='ACTIVE' AND lease_expires_at>? ORDER BY lease_expires_at",
                              (policy.group, now)).fetchall()
        if len(active) >= policy.max_concurrency:
            return Decision('HOST_BUSY', retry_at=active[0][0])
        token = secrets.token_hex(24)
        expires = number(now + lease, positive=True)
        if authorization is not None:
            expires = min(expires, authorization.expires_at)
        conn.execute("INSERT INTO acquisition_host_permits VALUES(?,?,?,?,?,'ACTIVE',?,?,NULL,?,?)",
                     (token, policy.group, policy.host, mode, owner, now, expires, policy.version,
                      json.dumps(asdict(authorization), sort_keys=True) if authorization else '{}'))
        conn.execute('UPDATE acquisition_host_state SET next_request_at=max(next_request_at,?) WHERE publisher_group=?',
                     (number(now + policy.min_delay_seconds), policy.group))
        return Decision('GRANTED', Permit(token, policy.group, policy.host, mode, owner, expires))


def owned(conn, permit, now):
    row = conn.execute("SELECT lease_expires_at FROM acquisition_host_permits WHERE permit_token=? AND publisher_group=? AND host=? AND acquisition_mode=? AND owner=? AND status='ACTIVE'",
                       (permit.token, permit.group, permit.host, permit.mode, permit.owner)).fetchone()
    if row is None or row[0] <= now:
        raise PermitLost('Permit expired, released or not owned')
    return row[0]


def renew(conn, permit, *, now, lease_seconds=60):
    now, lease = number(now), number(lease_seconds, positive=True)
    with transaction(conn):
        policy = reviewed_policy(conn, permit.host)
        _, now = state(conn, policy, now)
        deadline = owned(conn, permit, now)
        auth = json.loads(conn.execute('SELECT authorization_json FROM acquisition_host_permits WHERE permit_token=?', (permit.token,)).fetchone()[0])
        restricted = bool(auth) and permit.mode == 'browser' and permit.host in auth['restricted_hosts']
        if (policy.blocked or policy.max_concurrency == 0) and not restricted:
            raise PermitLost('Publisher policy no longer permits this request')
        if auth and now >= auth['expires_at']:
            raise PermitLost('Finite operator authorization expired')
        expires = max(deadline, number(now + lease, positive=True))
        if auth:
            expires = min(expires, auth['expires_at'])
        conn.execute('UPDATE acquisition_host_permits SET lease_expires_at=? WHERE permit_token=?', (expires, permit.token))
        return replace(permit, expires_at=expires)


def retry_after_seconds(raw, now):
    if raw is None:
        return None
    try:
        value = float(raw)
        return number(value)
    except (ValueError, TypeError, OverflowError):
        try:
            dt = parsedate_to_datetime(str(raw))
            if dt.tzinfo is None:
                return None
            return max(0.0, dt.timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return None


def finish(conn, permit, *, now, outcome='success', retry_after=None, cooldown_seconds=0):
    now, cooldown = number(now), number(cooldown_seconds)
    if outcome not in {'success', 'released', 'blocked', 'transport_failure'}:
        raise ValueError('Unknown permit outcome')
    with transaction(conn):
        policy = reviewed_policy(conn, permit.host)
        row, now = state(conn, policy, now)
        owned(conn, permit, now)
        failures = row[4] + 1 if outcome == 'transport_failure' else (0 if outcome == 'success' else row[4])
        delay = retry_after_seconds(retry_after, now)
        blocked = number(now + max(cooldown, delay if delay is not None else 0,
                            3600 if outcome == 'blocked' and delay is None and not cooldown else 0))
        circuit = number(now + 3600) if failures >= 3 and outcome == 'transport_failure' else 0
        # A success never clears an independently recorded future cooldown/circuit.
        conn.execute("UPDATE acquisition_host_permits SET status='RELEASED',released_at=? WHERE permit_token=?", (now, permit.token))
        conn.execute('UPDATE acquisition_host_state SET blocked_until=max(blocked_until,?),circuit_until=max(circuit_until,?),consecutive_failures=?,retry_after_raw=?,last_outcome=? WHERE publisher_group=?',
                     (blocked, circuit, failures, None if retry_after is None else str(retry_after), outcome, permit.group))
