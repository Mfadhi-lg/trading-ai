"""Verified chain completion for one issuer with an omitted intermediate.

No new root CA, network lookup, verification retry or global trust-store change.
The DigiCert intermediate was verified against the locked Certifi root bundle;
partial-chain trust is explicitly disabled so it cannot become a root anchor.
"""
import hashlib
import ssl
from pathlib import Path
from urllib.parse import urlsplit
import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

INTERMEDIATE = Path(__file__).parent/'certificates/rapidssl-tls-rsa-ca-g1.pem'
INTERMEDIATE_SHA256 = '48dc5154ee401053fcc4d22eff1881d81af249c27751820feb6c30ae623e9cf2'
ISSUER_HOST = 'www.sacheerome.com'


def verified_chain_context():
    pem = INTERMEDIATE.read_bytes()
    if hashlib.sha256(pem).hexdigest() != INTERMEDIATE_SHA256:
        raise ssl.SSLError('Pinned verified intermediate certificate changed')
    context = create_urllib3_context(cert_reqs=ssl.CERT_REQUIRED)
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    context.check_hostname = True
    context.load_verify_locations(cafile=certifi.where())
    context.load_verify_locations(cadata=pem.decode('ascii'))
    return context


class VerifiedChainAdapter(HTTPAdapter):
    def __init__(self):
        self.chain_context = verified_chain_context()
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs['ssl_context'] = self.chain_context
        return super().init_poolmanager(connections, maxsize, block=block, **kwargs)


def install_verified_chain(session, url):
    parsed = urlsplit(url)
    if parsed.scheme.lower() != 'https' or parsed.hostname != ISSUER_HOST:
        return
    # The shared worker dispatches in its isolated HTTP child. Offline fake
    # sessions remain pure fakes; real standalone Sessions use the same adapter.
    if isinstance(session, requests.Session):
        prefix = 'https://' + parsed.netloc + '/'
        if not isinstance(session.adapters.get(prefix), VerifiedChainAdapter):
            session.mount(prefix, VerifiedChainAdapter())
