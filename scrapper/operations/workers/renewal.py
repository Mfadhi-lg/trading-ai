"""Renew a live long job on a private connection; imports start no threads."""
from pathlib import Path
import threading
from ...db.database import connect
from ...db.document_work_claims import OwnershipLost, renew_queue, renew_extraction
from ...db.repository_backend import is_postgresql


class LeaseRenewal:
    def __init__(self, conn, callback, *, seconds=600):
        if seconds <= 0:
            raise ValueError('lease seconds must be positive')
        self.native = is_postgresql(conn)
        if self.native and not hasattr(conn, 'reopen'):
            raise ValueError('Worker renewal requires a PostgreSQL application connection')
        self.factory = conn.reopen if self.native else None
        self.path = None if self.native else conn.execute('PRAGMA database_list').fetchone()[2]
        self.callback = callback
        self.interval = seconds / 3
        self.stop = threading.Event()
        self.error = None
        self.thread = None

    def _run(self):
        conn = None
        try:
            # SQLite connections stay on the thread that opens them.
            conn = self.factory() if self.native else connect(Path(self.path))
            if not self.native:
                conn.execute('PRAGMA busy_timeout=1000')
            while not self.stop.wait(self.interval):
                self.callback(conn)
        except Exception as exc:
            self.error = exc
        finally:
            if conn is not None:
                conn.close()

    def __enter__(self):
        # In-memory fixtures have no independent connection; real roles use files.
        if self.native or self.path:
            self.thread = threading.Thread(target=self._run, name='claim-renewal', daemon=True)
            self.thread.start()
        return self

    def check(self, conn=None):
        if self.error is not None:
            raise OwnershipLost('Long-job renewal failed; refusing completion') from self.error
        if conn is not None:
            self.callback(conn)

    def __exit__(self, *_):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()


def queue_renewal(conn, kind, job, *, seconds=600, now=None):
    return LeaseRenewal(conn, lambda c: renew_queue(c, kind, job, seconds=seconds, now=now() if now else None), seconds=seconds)


def extraction_renewal(conn, claim, *, seconds=600):
    return LeaseRenewal(conn, lambda c: renew_extraction(c, claim, seconds=seconds), seconds=seconds)
