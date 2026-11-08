"""Explicit exit protocol: unknown failures retain the critical stop boundary."""
import json
import errno
import sqlite3
import requests

ENVIRONMENT = 75
JOB_FAILURE = 76
EXPIRED = 77
SOURCE_POLICY = 78


class ExecutionDeadline(BaseException):
    """Interrupt a whole bounded stage without becoming a per-job failure."""


def classify_exception(exc):
    message = str(exc)
    from scrapper.db.document_work_claims import OwnershipLost
    if isinstance(exc, (ExecutionDeadline, OwnershipLost)):
        return 'DEFERRED_ENVIRONMENT', ENVIRONMENT
    if (isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError))
            and not isinstance(exc, requests.exceptions.SSLError)):
        return 'DEFERRED_ENVIRONMENT', ENVIRONMENT
    if isinstance(exc,sqlite3.OperationalError) and message.lower() in ('database is locked','database table is locked','database is busy'):
        return 'DEFERRED_ENVIRONMENT',ENVIRONMENT
    if isinstance(exc,OSError) and exc.errno in (errno.ENOSPC,errno.ENODEV,errno.ENXIO):
        return 'DEFERRED_ENVIRONMENT',ENVIRONMENT
    if message.startswith('Unexpired production acquisition lease in '):
        # This guard runs after host ownership validation. Wait for expiry;
        # never clear another claimant's lease or turn it into a durable stop.
        return 'DEFERRED_ENVIRONMENT',ENVIRONMENT
    # A missing browser response consumes this slot but does not disable
    # future scheduled collection. Access denials and parser errors still stop.
    if message in ('BSE browser failed: BROWSER_TIMEOUT_OR_MISSING_RESULTS',
                   'BSE browser navigation timed out without a document'):
        return 'DEFERRED_ENVIRONMENT', ENVIRONMENT
    if isinstance(exc, BlockingIOError):
        return 'DEFERRED_ENVIRONMENT', ENVIRONMENT
    if any(text in message for text in (
        'Document-store capacity file is stale:',
        'Cannot inspect ownership: docker',
        'Exchange acquisition window reserved',
        'External document volume is not mounted',
        'Physical reserve cannot cover',
        'Reserve cannot cover document slot',
        'Target page, context or browser has been closed',
        'Initial external growth budget exhausted',
        'Scraper-attributed external growth budget exhausted',
    )):
        return 'DEFERRED_ENVIRONMENT', ENVIRONMENT
    if message in ('Initial scheduling window expired',
                   'Document scheduling disabled or outside authorized window'):
        return 'EXPIRED_AUTHORIZATION', EXPIRED
    return 'STOP', 2


def runtime_entrypoint(main):
    try:
        main()
        return 0
    except (Exception,ExecutionDeadline) as exc:
        status, code = classify_exception(exc)
        # Traceback is retained for every failure, including deferrals.
        import traceback
        traceback.print_exc()
        print(json.dumps({'stage':'execution_failure','status':status,
            'error_type':type(exc).__name__,'error':str(exc),
            'source_retry':False}),flush=True)
        return code
