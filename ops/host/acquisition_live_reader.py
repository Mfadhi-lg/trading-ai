"""Networkless monitor reader. SQL writes forbidden; only WAL sidecars writable.

The production directory must allow SQLite to create/read its shared WAL index.
Every database connection still uses URI mode=ro and PRAGMA query_only=ON.
"""
import json
from contextlib import contextmanager
import signal
import time
from pathlib import Path
import sys
sys.path[:0]=['/host','/activation','/app']
ALLOWED={'quarter_monitor','quarter_options','quarter_coverage_export','summary',
         'search','company','company_report','company_catalog','financials','download_diagnostics'}

def main():
    action=sys.argv[1];kwargs=json.loads(sys.argv[2])
    if action=='census':
        from acquisition_db_status import census
        value=census(Path('/data/scraper.db'),include_summary=bool(kwargs.get('summary')),live=True)
    elif action in ALLOWED:
        from acquisition_queries import inspect
        kwargs.pop('live',None)
        value=inspect(Path('/data/scraper.db'),action,live=True,**kwargs)
    else:raise ValueError('Unsupported monitor query')
    print(json.dumps(value))

@contextmanager
def wall_clock_budget(seconds):
    """Bound every reader phase, including connection setup and SQLite gates."""
    if not 0<seconds<=25:raise ValueError('Reader budget must be within 25 seconds')
    handler=signal.getsignal(signal.SIGALRM)
    old=signal.getitimer(signal.ITIMER_REAL)
    started=time.monotonic()
    def expired(*_):raise TimeoutError('Monitor reader wall-clock budget exhausted')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,min(seconds,old[0]) if old[0] else seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,handler)
        if old[0]:signal.setitimer(signal.ITIMER_REAL,max(.001,old[0]-(time.monotonic()-started)),old[1])


def run_reader(seconds=25):
    from postgresql_runtime import enabled, request_deadline
    with wall_clock_budget(seconds), request_deadline(seconds):
        if enabled():
            # Native readers use their own read-only MVCC transaction. The
            # SQLite sidecar gate cannot protect that database and can stall it.
            return main()
        import fcntl, time
        with Path('/activation/.monitor-readers.lock').open('rb') as gate:
            deadline=time.monotonic()+5
            while True:
                try:
                    fcntl.flock(gate,fcntl.LOCK_SH|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise RuntimeError('Monitor SQLite ownership gate busy')
                    time.sleep(.05)
            try:return main()
            finally:fcntl.flock(gate,fcntl.LOCK_UN)


if __name__=='__main__':
    run_reader()
