"""Cooperative request-boundary pause for the finite historical backfill."""
from contextlib import contextmanager
import fcntl
import os
import subprocess
import time

from .storage import ROOT,OUT,dump

STATE=OUT/'hcd02'
PAUSE=STATE/'lifecycle-paused.json'
SHARED_PAUSE=ROOT/'artifacts/automatic-acquisition/lifecycle-paused.json'


class PauseRequested(Exception):
    pass


def checkpoint():
    if PAUSE.exists() or SHARED_PAUSE.exists():
        raise PauseRequested('OWNER_LIFECYCLE_PAUSE')


def wait(seconds):
    deadline=time.monotonic()+seconds
    while True:
        checkpoint()
        remaining=deadline-time.monotonic()
        if remaining<=0:return
        time.sleep(min(.25,remaining))


@contextmanager
def worker(role):
    if role not in ('acquisition','reporter'):raise ValueError('unknown candle worker')
    with (STATE/f'.{role}-owner.lock').open('a+b') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        checkpoint()
        started=' '.join(subprocess.run(['/bin/ps','-p',str(os.getpid()),'-o','lstart='],capture_output=True,text=True,check=True).stdout.split())
        from datetime import datetime,timezone
        record=dict(pid=os.getpid(),process_started=started,role=role,state='RUNNING',
                    registered_at=datetime.now(timezone.utc).isoformat())
        dump(STATE/f'{role}-worker.json',record)
        try:
            yield
        finally:
            record.update(state='EXITED',exited_at=datetime.now(timezone.utc).isoformat())
            dump(STATE/f'{role}-worker.json',record)
