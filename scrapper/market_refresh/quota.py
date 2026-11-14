"""Cross-worker, durable conservative REST admission for one provider account."""
from datetime import datetime
import fcntl
import json
import math
from pathlib import Path

from experiments.historical_candle_archive_v1.storage import dump


def reserve(path, stamp):
    """Atomically reserve one call; False means busy/exhausted/clock rollback.

    All maintained REST workers for the same account must use the same explicit
    path. This gate cannot account for unrelated external clients. It never sleeps
    or retains credentials and counts attempted transport calls conservatively.
    """
    if isinstance(stamp, datetime):
        if stamp.utcoffset() is None:
            raise ValueError('aware quota timestamp required')
        stamp = stamp.timestamp()
    if isinstance(stamp, bool) or not isinstance(stamp, (int,float)) or not math.isfinite(stamp):
        raise ValueError('finite quota timestamp required')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name+'.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        state = json.loads(path.read_text()) if path.exists() else dict(calls=[])
        calls = state['calls']
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in calls):
            raise ValueError('corrupt shared quota ledger')
        if any(value > stamp for value in calls):
            return False
        recent = [value for value in calls if stamp-value < 1800]
        if (sum(stamp-value < 1 for value in recent) >= 1
                or sum(stamp-value < 60 for value in recent) >= 450 or len(recent) >= 1800):
            return False
        dump(path, dict(calls=recent+[stamp], limits=dict(per_second=1, per_minute=450, per_30_minutes=1800)))
        return True
