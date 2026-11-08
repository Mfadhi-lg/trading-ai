"""Shared browser publisher outcomes; unavailable is distinct from technical failure."""
class PublisherRateLimit(ValueError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__('HTTP 429: publisher rate limit; retained for retry')


class PublisherFileUnavailable(ValueError):
    """The publisher serves a missing-file notice instead of its report."""


