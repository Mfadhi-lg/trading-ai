"""Opt-in bounded Screener transport. No unmetered fallback or auth issuing."""
from urllib.parse import urlsplit, urljoin
import time
import requests
import certifi
from ...config import SCREENER_COOKIE_FILE, REQUEST_TIMEOUT
from ...core.http import ScreenerHostClient, validate_screener_result_screen_url, _classify_result_screen_response
from ...db.host_request_defer import require_shared_schema
from ...db.screener_host_defer import renew, owned, epoch
from ...db.repo import start_run, finish_run, store_blob, record_fetch
from ...documents.download.contracts import DownloadPolicy
from .host_requests import HostSession

class SharedScreenerClient(ScreenerHostClient):
    shared_permits = True
    def __init__(self, conn, *, owner, cookie_path=SCREENER_COOKIE_FILE,
                 check=lambda: None, now=time.time, sleep=time.sleep,
                 offline_session=None, wait_seconds=10, policy=None):
        require_shared_schema(conn)
        super().__init__(cookie_path)
        self.conn, self.owner = conn, owner
        self.external_check = check
        self.now = now
        self.claims = ()
        self.now_utc = None
        self.completed = []
        self.policy = policy or DownloadPolicy(max_bytes=8*1024*1024, total_timeout_seconds=REQUEST_TIMEOUT)
        self.transport = HostSession(conn, mode='screener', owner=owner, policy=self.policy,
            check=self.check, now=now, sleep=sleep, offline_session=offline_session, wait_seconds=wait_seconds)
        self.transport.cookies.update(self.session.cookies)
        self.session.close()  # Only its headers/cookie jar seed the bounded transport.

    def bind(self, claims, now_utc):
        self.claims, self.now_utc = tuple(claims), now_utc
        self.completed = []
        self.transport.dispatched = 0
        self.transport.responses = []

    def check(self):
        self.external_check()
        for kind, key, token in self.claims:
            renew(self.conn, kind, key, token, now=self.now_utc())

    def ownership_check(self, conn):
        self.external_check()
        for kind,key,token in self.claims:
            owned(conn,kind,key,token,now=self.now_utc())

    @staticmethod
    def validate(url):
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname != 'www.screener.in' or p.port is not None or p.username or p.password or p.fragment:
            raise ValueError('Shared Screener requests require the authenticated first-party HTTPS host')
        return url

    def _get(self, url, *, headers=None):
        current = self.validate(url)
        merged = dict(self.session.headers)
        merged.update(headers or {})
        for hop in range(self.policy.max_redirects+1):
            response = self.transport.get(current, headers=merged, timeout=REQUEST_TIMEOUT,
                allow_redirects=False, stream=True, verify=certifi.where())
            try:
                body = bytearray()
                for chunk in response.iter_content(self.policy.chunk_bytes):
                    body.extend(chunk)
                    if len(body) > self.policy.max_bytes:
                        response.failed = True
                        raise requests.RequestException('Screener response exceeded bounded byte limit')
                result = requests.Response()
                result.status_code, result.url = response.status_code, response.url
                result.headers = requests.structures.CaseInsensitiveDict(response.headers)
                result._content = bytes(body)
                result.encoding = requests.utils.get_encoding_from_headers(result.headers)
                self.completed.append((url, result))
            finally:
                response.close()
            if result.status_code not in {301,302,303,307,308} or not result.headers.get('Location'):
                return result
            if hop == self.policy.max_redirects:
                raise requests.TooManyRedirects('Screener redirect limit exceeded')
            current = self.validate(urljoin(current, result.headers['Location']))
            # Do not acquire login/register pages; return the observed redirect as rejected.
            if urlsplit(current).path.startswith(('/login','/register')):
                result.url = current
                return result
        raise AssertionError('Unreachable redirect loop')

    def fetch_result_screen(self, url):
        validate_screener_result_screen_url(url)
        response = self._get(url)
        return self._response(url,response,_classify_result_screen_response(response))

    def preserve_partial(self, *, snapshot_root, now_utc):
        # Only called when full ingestion cannot finish. Preserve original observed
        # responses without parsing, financial acceptance or artifact promotion.
        if not self.completed:
            return
        run = start_run(self.conn,'c20.4-partial',{'source':'screener','mode':'shared_partial','owner':self.owner})
        for url, response in self.completed:
            value = self._response(url,response,'rejected' if response.status_code >= 400 else 'ok')
            blob = store_blob(self.conn,value,'_SHARED_PARTIAL','standalone',now_utc,snapshot_root,resource_kind='shared_partial')
            record_fetch(self.conn,run,None,value,'standalone',now_utc,blob,resource_kind='shared_partial')
        finish_run(self.conn,run,'error')
        self.completed = []

    def close(self):
        self.transport.close()
