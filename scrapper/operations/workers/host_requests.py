"""Shared-permit session adapter for generic/rating executors.

Every GET/POST hop owns a permit until its HTTP child is dead. No DB transaction
spans I/O. Test transports must be synchronous, bounded offline fakes; real
HTTP always uses a killable process with parent-death fencing.
"""
import time
import requests
from ...db import host_permits
from ...db.document_work_claims import OwnershipLost
from ...documents.download.isolated_http import Hop


from ...core.host_request_contracts import AdministrativeDefer


class HostSession:
    def __init__(self, conn, *, mode, owner, policy, check, now=time.time,
                 sleep=time.sleep, offline_session=None, hop_factory=Hop,
                 wait_seconds=10, permit_api=host_permits, authorization=None):
        if mode not in {'generic', 'rating', 'browser', 'screener'}:
            raise ValueError('Unsupported shared acquisition mode')
        self.conn, self.mode, self.owner = conn, mode, owner
        self.permits, self.authorization = permit_api, authorization
        self.policy, self.check, self.now = policy, check, now
        self.sleep, self.offline_session, self.hop_factory = sleep, offline_session, hop_factory
        self.wait_seconds = wait_seconds
        if policy.total_timeout_seconds <= 0 or wait_seconds < 0:
            raise ValueError("Positive HTTP deadline and nonnegative host wait required")
        self.dispatched = 0
        self.responses = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def _check(self):
        self.check()
        if self.conn.in_transaction:
            raise ValueError('No transaction may span HTTP I/O')

    def _acquire(self, url):
        until = self.now() + self.wait_seconds
        while True:
            self._check()
            decision = self.permits.acquire(self.conn, url, mode=self.mode,
                owner=self.owner, now=self.now(), lease_seconds=self.policy.total_timeout_seconds+30,
                **({"authorization": self.authorization} if self.mode == "browser" else {}))
            if decision.permit:
                return decision.permit
            if decision.retry_at is None:
                decision = host_permits.Decision(decision.reason, retry_at=self.now()+3600)
            if decision.reason == 'PUBLISHER_POLICY_BLOCKED' or decision.retry_at > until:
                raise AdministrativeDefer(decision)
            self.sleep(min(.25, max(.01, decision.retry_at-self.now())))

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def request(self, method, url, **kwargs):
        if method not in {'GET', 'POST'} or kwargs.get('allow_redirects') is not False:
            raise ValueError('Shared requests require manually validated GET/POST hops')
        permit = self._acquire(url)
        response = _Response(self, permit)
        self.responses.append(response)
        try:
            response.checkpoint()
            # Count dispatch conservatively before creating the I/O child.
            self.dispatched += 1
            response.dispatched = True
            if hasattr(self.permits, "dispatch"):
                self.permits.dispatch(self.conn, permit)
            if self.offline_session is not None:
                response.fake = getattr(self.offline_session, method.lower())(url, **kwargs)
                response.status_code = response.fake.status_code
                response.url, response.headers = response.fake.url, response.fake.headers
            else:
                response.hop = self.hop_factory(method, url, kwargs, self.cookies, self.policy.chunk_bytes, response.deadline)
                message = response.receive()
                if message[0] != 'headers':
                    raise requests.RequestException('Missing HTTP headers')
                _, response.status_code, response.url, headers, self.cookies = message
                response.headers = requests.structures.CaseInsensitiveDict(headers)
            return response
        except BaseException:
            response.failed = True
            response.close()
            raise

    def deferred_result(self, requested_url, reason):
        from ...documents.download.contracts import DownloadResult, RedirectHop
        from ...documents.download.media import _safe_response_headers
        completed = [r for r in self.responses if r.status_code is not None]
        last = completed[-1] if completed else None
        redirects = tuple(RedirectHop(r.status_code, r.url, r.headers.get("Location"))
            for r in completed if r.status_code in {301,302,303,307,308})
        return DownloadResult(requested_url, last.url if last else None,
            last.status_code if last else None,
            _safe_response_headers(last.headers) if last else {}, redirects,
            None, None, None, None, "administrative_defer", 0, reason, reason)

    def close(self):
        for response in self.responses:
            response.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _Response:
    def __init__(self, session, permit):
        self.session, self.permit = session, permit
        self.deadline = time.monotonic()+min(session.policy.total_timeout_seconds,
            max(0, permit.expires_at-session.now()-5))
        self.hop = self.fake = None
        self.closed = self.failed = self.dispatched = False
        self.status_code = None
        self.headers = {}
        self.next_renewal = self.session.now()+5

    def checkpoint(self):
        try:
            self.session._check()
            if time.monotonic() >= self.deadline:
                raise requests.Timeout('Shared HTTP total deadline exceeded')
            if self.session.now() >= self.next_renewal:
                self.permit = self.session.permits.renew(self.session.conn, self.permit,
                    now=self.session.now(), lease_seconds=self.session.policy.total_timeout_seconds+30)
                self.next_renewal = self.session.now()+5
            else:
                self.session.permits.owned(self.session.conn, self.permit, self.session.now())
        except host_permits.PermitLost as exc:
            raise OwnershipLost('Shared host permit was lost') from exc

    def receive(self):
        while True:
            self.checkpoint()
            if self.hop.pipe.poll(.2):
                try:
                    message = self.hop.pipe.recv()
                except EOFError as exc:
                    raise requests.RequestException('HTTP child exited without response') from exc
                if message[0] == 'error':
                    error = {'Timeout': requests.Timeout, 'ConnectionError': requests.ConnectionError,
                             'SSLError': requests.exceptions.SSLError,
                             'ChunkedEncodingError': requests.exceptions.ChunkedEncodingError}.get(
                                 message[1], requests.RequestException)
                    raise error(message[2])
                return message
            if not self.hop.process.is_alive():
                raise requests.RequestException('HTTP child exited without response')

    def iter_content(self, chunk_size):
        try:
            if self.fake is not None:
                for chunk in self.fake.iter_content(chunk_size=chunk_size):
                    self.checkpoint()
                    yield chunk
            else:
                while True:
                    message = self.receive()
                    if message[0] == 'end':
                        return
                    if message[0] != 'chunk':
                        raise requests.RequestException('Invalid HTTP child protocol')
                    yield message[1]
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Never release capacity until physical I/O has terminated.
        if self.hop is not None:
            self.hop.close()
        if self.fake is not None:
            self.fake.close()
        status = self.status_code
        blocked = status in {401, 403, 429}
        retry_after = self.headers.get('Retry-After') or self.headers.get('retry-after')
        cooldown = 86400 if status in {401, 403} else 21600 if status == 429 else 0
        # Generic retains its existing Retry-After override; rating keeps its
        # conservative default as well as any longer Retry-After deadline.
        if self.session.mode == 'generic' and retry_after is not None:
            parsed = host_permits.retry_after_seconds(retry_after, self.session.now())
            if parsed:
                cooldown = parsed
        outcome = 'released' if not self.dispatched else 'blocked' if blocked else 'transport_failure' if self.failed or (status is not None and status >= 500) else 'success'
        try:
            self.session.permits.finish(self.session.conn, self.permit, now=self.session.now(),
                outcome=outcome, retry_after=retry_after, cooldown_seconds=cooldown)
        except host_permits.PermitLost as exc:
            raise OwnershipLost('Shared permit expired before completion') from exc
