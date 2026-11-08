"""One-based claim invocations, not counts of HTTP requests.

Browser compatibility rows expose the count before claim; other queues expose
it after claim. Administrative cooldown/release never calls this an HTTP count.
"""
BROWSER_MAX_INVOCATIONS = 4


def claim_attempt(job, kind):
    if kind not in ('generic', 'rating', 'browser'):
        raise ValueError('Unknown queue kind')
    raw = job['attempt_count']
    if type(raw) is not int or raw < (0 if kind == 'browser' else 1):
        raise ValueError('Invalid captured claim count')
    return raw + (kind == 'browser')


def retry_remaining(attempt, maximum):
    if type(attempt) is not int or attempt < 1:
        raise ValueError('Attempt must be a positive one-based invocation')
    if maximum is None:
        return True  # Direct rating retries retain their existing unbounded policy.
    if type(maximum) is not int or maximum < 1:
        raise ValueError('Maximum must be positive')
    return attempt < maximum


def retry_delay(attempt, *, first_seconds=900, maximum_seconds=86400, offset=0):
    retry_remaining(attempt, None)
    # Clamp before exponentiation: recovered/reset history may have large counts.
    return min(maximum_seconds, first_seconds * 2 ** min(attempt - 1 + offset, 16))
