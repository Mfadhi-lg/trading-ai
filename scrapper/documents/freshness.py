"""Finite result follow-up policy and evidence-based acquisition timings.

These helpers perform no I/O. They neither authorize polling nor infer that a
document was published from a result broadcast or its reporting quarter.
"""
from __future__ import annotations

from .availability import DEFAULT_RETRY_OFFSETS_HOURS, RetryPolicy, parse_utc


# Four extra checks in the first hour, then the existing six-week schedule.
# The first check is one minute after broadcast (or immediately due on intake).
NEAR_TERM_OFFSETS_HOURS = (1 / 60, 0.25, 0.5, 1, *DEFAULT_RETRY_OFFSETS_HOURS)


def result_retry_policy(*, broadcast_at_utc: str, observed_at_utc: str) -> RetryPolicy:
    age = (parse_utc(observed_at_utc) - parse_utc(broadcast_at_utc)).total_seconds()
    if 0 <= age <= 24 * 3600:
        # Preserve the original three long-tail unknown checks in addition to
        # the four bounded near-term attempts.
        return RetryPolicy(offsets_hours=NEAR_TERM_OFFSETS_HOURS, unknown_retry_limit=7)
    # Backfilled filings do not create an accelerated historical campaign.
    return RetryPolicy()


def measured_delays(rows, *, earlier: str, later: str, target_seconds=None):
    """Summarize retained timestamps; unknown/future evidence is never zero."""
    values = []
    unknown = 0
    for row in rows:
        try:
            seconds = (parse_utc(str(row[later])) - parse_utc(str(row[earlier]))).total_seconds()
            if seconds < 0:
                raise ValueError('Invalid timestamp order')
        except (ValueError, TypeError, KeyError):
            unknown += 1
            continue
        values.append(seconds)
    return {'status': 'OBSERVED' if values else 'UNKNOWN',
            'samples': len(values), 'unknown_samples': unknown,
            'maximum_seconds': max(values) if values else None,
            'mean_seconds': sum(values) / len(values) if values else None,
            'target_seconds': target_seconds,
            'within_target': (sum(value <= target_seconds for value in values)
                              if target_seconds is not None and values else None)}


def download_timings(rows):
    """Retained request throughput, distinct from wall-clock queue throughput."""
    attempts = list(rows)
    completed = [row for row in attempts if row['result_status'] == 'validated']
    elapsed_ms = sum(int(row['elapsed_ms']) for row in attempts)
    byte_count = sum(int(row['byte_size'] or 0) for row in completed)
    return {'status': 'OBSERVED' if attempts else 'UNKNOWN',
            'attempts': len(attempts), 'validated': len(completed),
            'elapsed_request_ms': elapsed_ms, 'validated_bytes': byte_count,
            'validated_bytes_per_request_second': byte_count * 1000 / elapsed_ms if elapsed_ms else None}
