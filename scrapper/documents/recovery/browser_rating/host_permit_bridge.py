"""Trusted finite-run permit broker. The host never receives a database handle."""
from dataclasses import asdict
import time
from ....core.host_policy import OperatorAuthorization
from ....db import host_permits
from ....db.host_request_defer import require_shared_schema
from ....db.browser_host_defer import defer, active


class Broker:
    def __init__(self, conn, run_id, authorization, *, now=time.time):
        require_shared_schema(conn)
        if not isinstance(authorization, OperatorAuthorization) or authorization.run_id != run_id:
            raise ValueError('Matching trusted finite operator receipt required')
        if not authorization.hosts or not authorization.reference.strip() or now() >= host_permits.number(authorization.expires_at,positive=True):
            raise ValueError('Finite operator receipt missing or expired')
        if not set(authorization.restricted_hosts) <= set(authorization.hosts):
            raise ValueError('Restricted-host receipt must be within the finite host scope')
        for host in authorization.hosts:
            if host_permits.policy_for(host).host != host:
                raise ValueError('Receipt hosts must be canonical exact host names')
        self.conn,self.authorization,self.now = conn,authorization,now
        self.permits = {}
        self.dispatched = {}
        self.dispatch_tokens = set()

    def setup(self):
        if self.now() >= self.authorization.expires_at:
            raise ValueError('Finite operator receipt expired')
        return {'shared_permits':True,'authorization':asdict(self.authorization),'server_now':self.now()}

    def handle(self, request, job):
        key = (job['browser_job_id'],job['claim_token'])
        action = request['action']
        if action == 'host_check':
            if self.now() >= self.authorization.expires_at:
                raise ValueError('Finite operator receipt expired')
            return {'owned':True}
        if action == 'host_acquire':
            decision = host_permits.acquire(self.conn,request['url'],mode='browser',
                owner=job['lease_owner'],now=self.now(),lease_seconds=150,
                authorization=self.authorization)
            if decision.permit:
                self.permits[decision.permit.token] = (key,decision.permit)
            return {'reason':decision.reason,'retry_at':decision.retry_at,
                'permit':asdict(decision.permit) if decision.permit else None}
        if action == 'host_defer':
            if any(k == key and active(self.conn,p.token)
                for k,p in self.permits.values()):
                raise ValueError('Terminate requests and finish permits before deferring')
            retry_at = host_permits.number(request['retry_at'])
            if retry_at <= self.now(): retry_at = self.now()+1
            defer(self.conn,job,now=self.now(),retry_at=retry_at,
                dispatched=self.dispatched.get(key,0),reason=request['reason'],
                completed_requests=request.get('completed_requests',[]))
            return {'status':'PENDING','administrative_defer':request['reason'],'retry_at':retry_at}
        stored = self.permits.get(request.get('permit_token'))
        if stored is None or stored[0] != key:
            raise ValueError('Permit is not owned by this browser claim')
        permit = stored[1]
        if action == 'host_dispatch':
            if permit.token in self.dispatch_tokens:
                raise ValueError('One dispatch per browser permit required')
            host_permits.owned(self.conn,permit,self.now())
            self.dispatch_tokens.add(permit.token)
            self.dispatched[key] = self.dispatched.get(key,0)+1
            return {'dispatched':True}
        if action == 'host_renew':
            permit = host_permits.renew(self.conn,permit,now=self.now(),lease_seconds=150)
            self.permits[permit.token] = (key,permit)
            return {'permit':asdict(permit)}
        if action == 'host_owned':
            return {'expires_at':host_permits.owned(self.conn,permit,self.now())}
        if action == 'host_finish':
            host_permits.finish(self.conn,permit,now=self.now(),outcome=request['outcome'],
                retry_after=request.get('retry_after'),cooldown_seconds=request.get('cooldown_seconds',0))
            return {'released':True}
        raise ValueError('Unsupported host permit action')

    def assert_drained(self, job):
        key = (job['browser_job_id'],job['claim_token'])
        if any(k == key and active(self.conn,p.token) for k,p in self.permits.values()):
            raise ValueError('Finish all browser requests before queue completion')
