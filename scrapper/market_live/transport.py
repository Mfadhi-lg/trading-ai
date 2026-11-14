"""Secure V3 authorization and lazily imported WebSocket transport."""
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class AuthenticationBlocked(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def connect(token_supplier, admission=lambda: None):
    # Never forward bearer tokens to redirects or persist authorized socket URLs.
    token = token_supplier()
    admission()
    request = Request('https://api.upstox.com/v3/feed/market-data-feed/authorize',
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    try:
        with build_opener(NoRedirect).open(request,timeout=20) as response:
            payload=json.loads(response.read(65536))
    except HTTPError as error:
        if error.code in (401,403): raise AuthenticationBlocked() from None
        raise ConnectionError('authorization endpoint unavailable') from None
    url=payload['data']['authorized_redirect_uri']
    parsed=urlsplit(url)
    if parsed.scheme!='wss' or not parsed.hostname or not parsed.hostname.endswith('.upstox.com'):
        raise ValueError('untrusted authorized WebSocket endpoint')
    admission()
    from websockets.sync.client import connect as websocket_connect
    return Socket(websocket_connect(url,open_timeout=20,ping_interval=20,ping_timeout=20,
                             close_timeout=2,max_size=8*1024*1024))


class Socket:
    """Normalize library disconnects without exposing authenticated exception text."""
    def __init__(self, socket): self.socket=socket
    def __enter__(self): return self
    def __exit__(self,*args): self.socket.close()
    def send(self, raw):
        from websockets.exceptions import ConnectionClosed
        try: self.socket.send(raw)
        except ConnectionClosed: raise ConnectionError('WebSocket disconnected') from None
    def recv(self, **kwargs):
        from websockets.exceptions import ConnectionClosed
        try: return self.socket.recv(**kwargs)
        except ConnectionClosed: raise ConnectionError('WebSocket disconnected') from None
