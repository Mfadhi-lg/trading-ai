"""Finite browser-run ledger operations behind the compatible command facade.

Clock and retry hooks are local arguments so existing command patch points remain
usable without mutating shared module globals.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ....db.documents.recovery import browser_rating as repository
from ....db.repo import utc_now

AGENCY_HOSTS = (
    'www.icra.in', 'www.crisil.com', 'www.crisilratings.com',
    'www.indiaratings.co.in', 'www.acuite.in', 'connect.acuite.in',
    'www.infomerics.com', 'bcrisp.in', 'www.bcrisp.in',
    'www.brickworkratings.com', 'www.careratings.com',
)


SETTLED = ('SKIPPED_POLICY', 'TERMINAL_FAILURE', 'RETRYABLE_FAILURE', 'INTERACTION_REQUIRED')


def retry_delay(value, consecutive=1, *, datetime=datetime, parsedate_to_datetime=parsedate_to_datetime):
    if value:
        try:
            return max(60, int(value))
        except ValueError:
            try:
                return max(60, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
            except (ValueError, TypeError):
                pass
    return min(6 * 3600, 3600 * 2 ** min(consecutive - 1, 3))


def worker_state(conn, run_id, owner, status, *, utc_now=utc_now):
    return repository.worker_state(conn, run_id, owner, status, utc_now=utc_now)


def release_owned_jobs(conn, run_id, owner, *, utc_now=utc_now):
    return repository.release_owned_jobs(conn, run_id, owner, utc_now=utc_now)


def seed(conn, run_id, *, utc_now=utc_now):
    return repository.seed(conn, run_id, utc_now=utc_now, agency_hosts=AGENCY_HOSTS, settled=SETTLED)


def report(conn, run_id):
    return repository.report(conn, run_id)


def claim(conn, run_id, hosts, owner, job_ids=(), *, utc_now=utc_now, datetime=datetime):
    return repository.claim(conn, run_id, hosts, owner, job_ids, utc_now=utc_now, datetime=datetime)


def finish(conn, job, status, content_id=None, final_url=None, error=None, evidence=None, *, utc_now=utc_now):
    return repository.finish(conn, job, status, content_id, final_url, error, evidence, utc_now=utc_now)


def cool_down(conn, run_id, host, retry_after=None, *, utc_now=utc_now, datetime=datetime, retry_delay=retry_delay):
    return repository.cool_down(conn, run_id, host, retry_after, utc_now=utc_now, datetime=datetime, retry_delay=retry_delay)


