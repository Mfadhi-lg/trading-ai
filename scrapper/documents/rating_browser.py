"""Recurring CRISIL/ICRA acquisition on the canonical rating queue."""
from dataclasses import replace
import time
import sqlite3

from ..core.access_policy import host_matches_policy
from ..operations.workers.browser_requests import BrowserRequests
from .credit_rating_resolver import CreditRatingResolution, ResolutionStep
from .recovery.browser_rating.errors import PublisherFileUnavailable


BROWSER_RATING_HOSTS = ('crisil.com', 'crisilratings.com', 'icra.in')
BROWSER_REQUEST_LIMIT = 12


def uses_rating_browser(target):
    return host_matches_policy(target, BROWSER_RATING_HOSTS)


def browser_host_sql(column):
    """Internal SQL expression; callers supply a fixed source-code column name."""
    if column not in ('q.target_host', 'queue.target_host'):
        raise ValueError('Unsupported browser routing column')
    return '(' + ' OR '.join(
        f"({column}='{host}' OR {column} LIKE '%.{host}')"
        for host in BROWSER_RATING_HOSTS) + ')'


class RatingBrowserRequests(BrowserRequests):
    """Use the rating claim's shared session for every browser HTTP request.

    The canonical rating worker owns renewal, retries and permit deferral; no
    separate browser run, operator receipt or recovery ledger is needed.
    """
    def __init__(self, session, url, *, request_limit=BROWSER_REQUEST_LIMIT,
                 monotonic=time.monotonic):
        if type(request_limit) is not int or request_limit < 1:
            raise ValueError('Positive browser request limit required')
        self.session = session
        self.context = None
        self.failure = None
        self.monotonic = monotonic
        self.deadline = monotonic() + session.policy.total_timeout_seconds
        self.request_limit = request_limit
        self.requests = 0
        self.redirects = 0
        self.hosts = ('icra.in',) if host_matches_policy(url, ('icra.in',)) else (
            'crisil.com', 'crisilratings.com')

    def check(self):
        if self.failure is not None:
            raise self.failure
        self.session._check()
        if self.monotonic() >= self.deadline:
            raise TimeoutError('Rating browser total deadline exceeded')

    def fetch(self, method, url, **kwargs):
        self.check()
        if not host_matches_policy(url, self.hosts):
            raise ValueError('Rating browser navigation left its publisher')
        if self.requests >= self.request_limit:
            raise RuntimeError('Rating browser request budget exhausted')
        self.requests += 1
        original = self.session.policy
        self.session.policy = replace(original, total_timeout_seconds=min(
            original.total_timeout_seconds, max(.001, self.deadline-self.monotonic())))
        try:
            result = super().fetch(method, url, **kwargs)
            if result[0] in {301,302,303,307,308}:
                self.redirects += 1
                if self.redirects > original.max_redirects:
                    raise ValueError('Rating browser redirect limit exceeded')
            return result
        finally:
            self.session.policy = original

    def route(self, route):
        # External analytics/assets are unnecessary to acquire a publisher report.
        if (route.request.resource_type != 'document'
                and not host_matches_policy(route.request.url, self.hosts)):
            route.abort()
            return
        return super().route(route)


def resolve_rating_browser(conn, job, *, session, policy, playwright_factory=None,
                           fetch_report=None, monotonic=time.monotonic,
                           request_limit=BROWSER_REQUEST_LIMIT):
    """Acquire and validate one report, retaining ordinary resolution evidence."""
    from ..db.document_work_claims import OwnershipLost
    from ..core.host_request_contracts import AdministrativeDefer
    from .rating_identity import company_names
    if not uses_rating_browser(job['requested_url']):
        raise ValueError('Recurring browser acquisition is limited to CRISIL/ICRA')
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright
        playwright_factory = sync_playwright
    if fetch_report is None:
        from ..sources.documents.browser_rating import fetch_report
    company = conn.execute('''SELECT c.current_legal_name FROM document_artifacts a
        JOIN companies c USING(company_id) WHERE a.artifact_id=?''',
        (job['artifact_id'],)).fetchone()
    adapter = RatingBrowserRequests(session, job['requested_url'], monotonic=monotonic,
                                    request_limit=request_limit)
    started = monotonic()
    try:
        session._check()
        with playwright_factory() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                result, evidence = fetch_report(browser, job['requested_url'], company[0],
                    timeout_ms=max(1, int(policy.total_timeout_seconds * 1000)),
                    aliases=company_names(conn, job['artifact_id']),
                    shared_requests=adapter, policy=policy)
                adapter.check()
            finally:
                browser.close()
        if not result.body or len(result.body) > policy.max_bytes:
            raise ValueError('Empty or oversized browser report')
        evidence = dict(evidence, method='automated_credit_rating_browser',
                        browser_requests=adapter.requests,
                        browser_request_limit=adapter.request_limit)
        status = 'BROWSER_PDF_VALIDATED' if result.detected_media_type == 'application/pdf' else 'BROWSER_HTML_REPORT_VALIDATED'
        return CreditRatingResolution(status, job['requested_url'], result, (
            ResolutionStep('automated_browser', 'BROWSER', job['requested_url'], result, True, evidence),))
    except (AdministrativeDefer, OwnershipLost, sqlite3.DatabaseError):
        raise
    except Exception as error:
        # Preserve HTTP status for the existing 401/403/429 host cooldown logic.
        result = replace(session.deferred_result(job['requested_url'], 'browser_failure'),
                         result_status='browser_failure',
                         elapsed_ms=int((monotonic()-started)*1000))
        status = ('TERMINAL_INVALID_CONTENT' if isinstance(error, PublisherFileUnavailable)
                  else 'INTERACTION_REQUIRED' if 'REVIEW_REQUIRED' in str(error)
                  else 'RETRYABLE_BROWSER_FAILURE')
        return CreditRatingResolution(status, job['requested_url'], None, (
            ResolutionStep('automated_browser', 'BROWSER', job['requested_url'], result, False,
                           {'method':'automated_credit_rating_browser',
                            'browser_requests':adapter.requests,
                            'browser_request_limit':adapter.request_limit}),),
            f'{type(error).__name__}: {error}')
    finally:
        adapter.close()
