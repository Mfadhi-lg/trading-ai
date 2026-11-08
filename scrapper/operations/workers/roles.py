"""Continuous owners for five proven bounded acquisition loops.

No subprocesses, queue implementation, broad-scope option or import-time I/O.
Each batch retains its existing CLI handler, fresh connection and report.
"""
from dataclasses import dataclass
import json
import sys
from .lifecycle import WorkerLifecycle, role_lifecycle, stderr_heartbeat


@dataclass(frozen=True)
class Role:
    command: tuple[str, ...]
    delay: int
    startup: tuple[str, ...] = ()


ROLES = {
    "document-download-worker": Role((
        "run-document-download-worker", "--eligible-scope", "accepted-screener-concall",
        "--limit", "25", "--throttle-seconds", "1"), 2,
        ("queue-accepted-document-downloads",)),
    "rating-recovery-download": Role((
        "run-document-download-worker", "--eligible-scope", "da06-rating-recovery",
        "--artifact-type", "credit_rating", "--limit", "25", "--throttle-seconds", "1"), 2),
    **{f"credit-rating-worker-{instance}": Role((
        "run-credit-rating-download-worker", "--limit", "25", "--rating-instance", instance,
        "--throttle-seconds", "4"), 10) for instance in ("care", "india-ratings", "other")},
}


def idle_health(db_path, role, lifecycle, snapshot):
    from ...config import DOCUMENT_STORE
    from ...db.database import connect
    from ...documents.store import assert_document_store_ready
    from .health import role_health
    conn = connect(db_path, read_only=True)
    try:
        return role_health(conn, role, snapshot, now=lifecycle.clock(),
            dependencies={"document_store": lambda: bool(assert_document_store_ready(DOCUMENT_STORE))})
    finally:
        conn.close()


def run_role(name, *, db_path, dispatch=None, lifecycle=None, max_cycles=None):
    """Run fixed post-batch cadence; finite cycles are fixture-only Python API.

    Error batches retain shell retry cadence and report failures; SIGTERM/INT
    drains the active bounded worker, then skips the next delay and batch.
    """
    if name not in ROLES:
        raise ValueError(f"Unknown acquisition role: {name!r}")
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
        raise ValueError("max_cycles must be a positive integer")
    if dispatch is None:
        from ...cli import main
        dispatch = main
    if lifecycle is None:
        with WorkerLifecycle(observer=stderr_heartbeat).signals() as owned:
            return run_role(name, db_path=db_path, dispatch=dispatch,
                            lifecycle=owned, max_cycles=max_cycles)
    role = ROLES[name]
    previous_probe = lifecycle.health_probe
    lifecycle.health_probe = lambda snapshot: idle_health(
        db_path, "generic" if name in ("document-download-worker", "rating-recovery-download") else "rating",
        lifecycle, snapshot)
    cycles = 0
    errors = 0
    def invoke(command):
        nonlocal errors
        try:
            code = dispatch([*command, "--db", str(db_path)])
        except Exception as exc:
            # Like the prior non-set-e shell, a failed command does not retire
            # this continuous role. Never swallow process interrupts/exits.
            code = 2
            print(json.dumps({"status": "error", "role": name,
                              "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True),
                  file=sys.stderr, flush=True)
        if lifecycle.active_job is not None:
            # An abnormal executor exit must not reuse a lifecycle that still
            # owns work. Let container restart/lease expiry recover it.
            raise RuntimeError("Bounded executor returned with unfinished owned work")
        if code:
            errors += 1
            print(json.dumps({"role": name, "batch_exit_code": code}, sort_keys=True),
                  file=sys.stderr, flush=True)
        return code
    try:
        with role_lifecycle(lifecycle):
            if role.startup and not lifecycle.is_set():
                invoke(role.startup)
            while not lifecycle.is_set():
                invoke(role.command)
                cycles += 1
                # Preserve a report per batch even on piped/container stdout.
                sys.stdout.flush()
                if lifecycle.is_set() or (max_cycles is not None and cycles >= max_cycles):
                    break
                lifecycle.wait(role.delay, state="WAITING")
    finally:
        lifecycle.health_probe = previous_probe
    return {"role": name, "cycles": cycles, "error_batches": errors,
            "status": "drained" if lifecycle.is_set() else "complete"}
