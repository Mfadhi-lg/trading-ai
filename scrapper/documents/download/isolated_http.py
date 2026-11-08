"""One killable HTTP hop; imports perform no I/O or process startup.

The parent owns permits and the deadline. This child owns no DB or content
store. Its bounded pipe streams bytes to the existing validation/store owner.
"""
import multiprocessing
import os
import signal
import sys
import time
import threading
import requests
from .tls import install_verified_chain


def request_error_kind(exc):
    # Preserve network vs certificate failures across the child process pipe.
    for kind in (requests.exceptions.SSLError, requests.Timeout, requests.ConnectionError,
                 requests.exceptions.ChunkedEncodingError):
        if isinstance(exc, kind):
            return kind.__name__
    return 'RequestException'


def http_hop(pipe, parent_pid, method, url, kwargs, cookies, chunk_bytes, deadline):
    # Linux roles: kill the child if its permit-owning parent is SIGKILLed.
    # The post-prctl check closes the fork/spawn-to-registration race.
    if sys.platform != 'linux':
        pipe.send(('error', 'RequestException', 'Shared HTTP requires Linux parent-death fencing'))
        pipe.close()
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        pipe.close()
        return
    if time.monotonic() >= deadline:
        pipe.close()
        return
    timer = threading.Timer(deadline-time.monotonic(), lambda: os.kill(os.getpid(), signal.SIGKILL))
    timer.daemon = True
    timer.start()
    try:
        with requests.Session() as session:
            session.cookies.update(cookies)
            install_verified_chain(session, url)
            with session.request(method, url, **kwargs) as response:
                pipe.send(('headers', response.status_code, response.url,
                           dict(response.headers), session.cookies))
                for chunk in response.iter_content(chunk_size=chunk_bytes):
                    if chunk:
                        pipe.send(('chunk', chunk))
                pipe.send(('end',))
    except requests.RequestException as exc:
        pipe.send(('error', request_error_kind(exc), str(exc)))
    finally:
        timer.cancel()
        pipe.close()


class Hop:
    def __init__(self, method, url, kwargs, cookies, chunk_bytes, deadline):
        if sys.platform != 'linux':
            raise RuntimeError('Shared HTTP requires Linux parent-death fencing')
        context = multiprocessing.get_context('spawn')
        self.pipe, child = context.Pipe(duplex=False)
        self.process = context.Process(target=http_hop,
            args=(child, os.getpid(), method, url, kwargs, cookies, chunk_bytes, deadline))
        try:
            self.process.start()
        finally:
            child.close()

    def close(self):
        if self.process.is_alive():
            self.process.kill()
        self.process.join()
        self.pipe.close()
