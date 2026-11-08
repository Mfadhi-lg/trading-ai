"""Opt-in browser routing through bounded, killable shared HTTP children.

No route continues or route.fetch calls: redirects are fulfilled back to the
browser and intercepted afresh. APIRequestContext and WebSockets are forbidden.
The finite receipt and all permit decisions belong to the trusted DB bridge.
"""
from dataclasses import asdict
from types import SimpleNamespace
import time
from urllib.parse import urljoin, urlsplit
from scrapper.core.attempts import claim_attempt
from scrapper.core.host_request_contracts import AdministrativeDefer
from scrapper.db.host_permits import Permit, Decision, PermitLost
from scrapper.documents.downloader import DownloadPolicy
from scrapper.operations.workers.host_requests import HostSession


class RemotePermits:
    def __init__(self, exchange, job):
        self.exchange = exchange
        self.identity = {'browser_job_id':job['browser_job_id'],
            'claim_attempt_count':claim_attempt(job,'browser'),'claim_token':job['claim_token']}

    def call(self, action, **values):
        return self.exchange(dict(self.identity,action=action,**values))

    def acquire(self, conn, url, **kwargs):
        result = self.call('host_acquire',url=url)
        return Decision(result['reason'], Permit(**result['permit']) if result['permit'] else None,result['retry_at'])

    def dispatch(self, conn, permit):
        self.call('host_dispatch',permit_token=permit.token)

    def owned(self, conn, permit, now):
        try: return self.call('host_owned',permit_token=permit.token)['expires_at']
        except ValueError as error: raise PermitLost(str(error)) from error

    def renew(self, conn, permit, **kwargs):
        try: return Permit(**self.call('host_renew',permit_token=permit.token)['permit'])
        except ValueError as error: raise PermitLost(str(error)) from error

    def finish(self, conn, permit, **kwargs):
        kwargs.pop('now',None)
        try: return self.call('host_finish',permit_token=permit.token,**kwargs)
        except ValueError as error: raise PermitLost(str(error)) from error


def handshake(exchange, authorization, *, now=time.time):
    import sys
    if sys.platform != 'linux':
        raise RuntimeError('Shared browser transport requires Linux parent-death fencing')
    result = exchange({'action':'shared_setup'})
    expected = asdict(authorization)
    # JSON changes tuple receipts to lists; compare their wire representations.
    import json
    expected = json.loads(json.dumps(expected))
    if result.get('shared_permits') is not True or result.get('authorization') != expected:
        raise ValueError('Shared bridge must match the trusted finite operator receipt')
    if abs(result['server_now']-now()) > 2 or authorization.expires_at <= now()+5:
        raise ValueError('Shared browser clock skew or expired finite receipt')


class BrowserRequests:
    def __init__(self, exchange, job, stopping, *, authorization, offline_session=None,
                 now=time.time, sleep=time.sleep, hop_factory=None):
        self.remote = RemotePermits(exchange,job)
        self.failure = None
        self.now = now
        self.stopping = stopping
        self.context = None
        def check():
            if stopping.is_set(): raise RuntimeError('Browser worker stopping')
            self.remote.call('host_check')
        values = {'hop_factory':hop_factory} if hop_factory else {}
        self.session = HostSession(SimpleNamespace(in_transaction=False),mode='browser',
            owner=job['lease_owner'],policy=DownloadPolicy(total_timeout_seconds=120),
            check=check,permit_api=self.remote,authorization=authorization,
            offline_session=offline_session,now=now,sleep=sleep,**values)

    def install(self, context, *, documents_only=False):
        # Fail closed on old Playwright versions rather than allowing unmetered WS.
        if not callable(getattr(context,'route_web_socket',None)):
            raise RuntimeError('Shared browser requires WebSocket interception support')
        self.context = context
        self.documents_only = documents_only
        context.route_web_socket('**/*',lambda socket:socket.close())
        context.route('**/*',self.route)

    def check(self):
        if self.failure is not None: raise self.failure
        self.session._check()

    def fetch(self, method, url, *, headers=None, data=None):
        if urlsplit(url).scheme not in {'http','https'} or method not in {'GET','POST'}:
            raise ValueError('Shared browser supports only HTTP(S) GET/POST')
        response = None
        try:
            kwargs = {'allow_redirects':False,'stream':True,'timeout':(15,30),
                'headers':headers or {}}
            if data is not None: kwargs['data'] = data
            response = self.session.request(method,url,**kwargs)
            body = bytearray()
            for chunk in response.iter_content(self.session.policy.chunk_bytes):
                body.extend(chunk)
                if len(body) > self.session.policy.max_bytes:
                    raise ValueError('Browser response exceeds byte limit')
            # requests decompressed the payload. These transport headers are stale.
            headers = {k:v for k,v in response.headers.items() if k.lower() not in {
                'content-encoding','content-length','transfer-encoding','connection'}}
            return response.status_code,response.url,headers,bytes(body)
        finally:
            if response is not None: response.close()

    def route(self, route):
        request = route.request
        if request.resource_type in {'image','font','media'} or (getattr(self,'documents_only',False) and request.resource_type != 'document'):
            route.abort(); return
        try:
            self.check()
            status,url,headers,body = self.fetch(request.method,request.url,
                headers=request.all_headers(),data=request.post_data_buffer)
            if self.context is not None:
                self.sync_cookies()
                headers = {k:v for k,v in headers.items() if k.lower()!='set-cookie'}
            route.fulfill(status=status,headers=headers,body=body)
        except Exception as error:
            self.failure = error
            route.abort()

    def sync_cookies(self):
        cookies = []
        for cookie in self.session.cookies:
            item = {'name':cookie.name,'value':cookie.value,'path':cookie.path or '/',
                'secure':cookie.secure,'httpOnly':'HttpOnly' in cookie._rest}
            if cookie.domain_specified:
                item['domain'] = cookie.domain
            else:
                item.pop('path')
                item['url'] = ('https://' if cookie.secure else 'http://')+cookie.domain+(cookie.path or '/')
            if cookie.expires is not None:
                item['expires'] = cookie.expires
            same_site = cookie._rest.get('SameSite')
            if same_site and same_site.lower() in {'strict','lax','none'}:
                item['sameSite'] = same_site.title()
            cookies.append(item)
        if cookies: self.context.add_cookies(cookies)

    def viewer(self, url):
        # APIRequestContext bypasses context routes; use this metered path instead.
        for _ in range(self.session.policy.max_redirects+1):
            headers = {}
            if self.context is not None:
                cookies = self.context.cookies([url])
                headers['Cookie'] = '; '.join(c['name']+'='+c['value'] for c in cookies)
            status,final,headers,body = self.fetch('GET',url,headers=headers)
            if self.context is not None: self.sync_cookies()
            if status not in {301,302,303,307,308}: return status,final,headers,body
            location = next((v for k,v in headers.items() if k.lower()=='location'),None)
            if not location: raise ValueError('Viewer redirect missing Location')
            url = urljoin(final,location)
        raise ValueError('Viewer redirect limit exceeded')

    def defer(self, error):
        self.close()
        return self.remote.call('host_defer',reason=error.reason,
            retry_at=error.retry_at or self.now()+3600,
            completed_requests=[{'url':r.url,'http_status':r.status_code,
                'location':r.headers.get('Location')} for r in self.session.responses
                if r.status_code is not None])

    def close(self):
        self.session.close()
