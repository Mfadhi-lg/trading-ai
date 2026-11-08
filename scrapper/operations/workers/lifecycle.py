"""Cooperative, thread-free lifecycle for bounded workers.

Heartbeats observe loop liveness, never renew a lease or prove job progress.
No watchdog interrupts network/OCR or closes an unowned durable claim.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import signal
import sys
import threading
import time


class WorkerLifecycle:
    def __init__(self, *, event=None, monotonic=time.monotonic,
                 heartbeat_seconds=5, observer=None, signal_api=signal):
        if not math.isfinite(heartbeat_seconds) or heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be finite and positive")
        self.event = event if event is not None else threading.Event()
        self.clock = monotonic
        self.interval = heartbeat_seconds
        self.observer = observer
        self.signal_api = signal_api
        self.state = "STARTING"
        self.active_job = None
        self.completed = 0
        self.progress_at = self.clock()
        self.heartbeat_at = self.progress_at
        self.observer_error = None
        self.health_probe = None
        self.claimed_at = None

    def snapshot(self):
        snapshot = {"state": "DRAINING" if self.is_set() and self.state != "STOPPED" else self.state,
                "active_job": self.active_job, "completed": self.completed,
                "heartbeat_at_monotonic": self.heartbeat_at,
                "progress_at_monotonic": self.progress_at,
                "observer_error": self.observer_error}
        if self.claimed_at is not None:
            snapshot["claimed_at_monotonic"] = self.claimed_at
        if self.health_probe is not None:
            try:
                snapshot["health"] = self.health_probe(snapshot)
            except Exception as exc:
                snapshot["health"] = {"healthy": False, "errors": ["probe:" + type(exc).__name__]}
        return snapshot

    def beat(self, state=None):
        if state is not None:
            self.state = state
        self.heartbeat_at = self.clock()
        if self.observer is not None:
            try:
                self.observer(self.snapshot())
                self.observer_error = None
            except Exception as exc:
                # Observability failure must not turn completed work into retries.
                self.observer_error = type(exc).__name__

    def set(self):
        # Signal handler only requests drain. No I/O, DB or callbacks here.
        self.event.set()

    def is_set(self):
        return self.event.is_set()

    def claimed(self, job_id):
        if self.active_job is not None:
            raise RuntimeError("worker already owns active work")
        self.active_job = job_id
        self.claimed_at = self.clock()
        self.beat("WORKING")

    def finished(self):
        self.active_job = None
        self.claimed_at = None
        self.completed += 1
        self.progress_at = self.clock()
        self.beat("READY")

    def wait(self, seconds, *, state="WAITING", interruptible=True):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("wait must be finite and nonnegative")
        deadline = self.clock() + seconds
        self.beat(state)
        while True:
            if interruptible and self.is_set():
                return True
            remaining = deadline - self.clock()
            if remaining <= 0:
                return self.is_set()
            duration = min(self.interval, remaining)
            if interruptible:
                if self.event.wait(duration):
                    self.beat()
                    return True
            else:
                # A claimed request must preserve its publisher pacing even
                # after drain. Avoid spinning on an already-set Event.
                time.sleep(duration)
            self.beat()

    @contextmanager
    def signals(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("signal scope requires the main thread")
        previous = {}
        try:
            for sig in (self.signal_api.SIGTERM, self.signal_api.SIGINT):
                previous[sig] = self.signal_api.signal(sig, lambda *_: self.set())
            self.beat("READY")
            yield self
        finally:
            for sig, handler in previous.items():
                self.signal_api.signal(sig, handler)
            self.beat("STOPPED")


def stderr_heartbeat(snapshot):
    print(json.dumps({"worker_lifecycle": snapshot}, sort_keys=True), file=sys.stderr, flush=True)


_role_lifecycle = ContextVar("acquisition_role_lifecycle", default=None)


@contextmanager
def role_lifecycle(lifecycle):
    """Share one drain request across repeated bounded CLI dispatches."""
    token = _role_lifecycle.set(lifecycle)
    try:
        yield lifecycle
    finally:
        _role_lifecycle.reset(token)


def run_bounded(worker, *args, **kwargs):
    """CLI role boundary; legacy stdout/return value and finite limit stay intact."""
    active = _role_lifecycle.get()
    if active is not None:
        previous_probe = active.health_probe
        try:
            return worker(*args, lifecycle=active, **kwargs)
        finally:
            # Batch connections close in their CLI handlers. The role's idle
            # probe uses a fresh read-only connection between batches.
            active.health_probe = previous_probe
    with WorkerLifecycle(observer=stderr_heartbeat).signals() as lifecycle:
        return worker(*args, lifecycle=lifecycle, **kwargs)
