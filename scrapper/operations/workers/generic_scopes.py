"""Explicit common-executor scope policy; never expand None into authorization."""
ELIGIBLE_SCOPES = {
    "accepted-screener-concall": ("transcript", "presentation"),
    "da06-rating-recovery": ("credit_rating",),
}
TURNS = ("accepted-screener-concall",) * 3 + ("da06-rating-recovery",)


def validate_scopes(scopes):
    scopes = tuple(scopes)
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("eligible scopes must be explicit, nonempty and unique")
    if any(scope not in ELIGIBLE_SCOPES for scope in scopes):
        raise ValueError("scope is not eligible for the common generic executor")
    return scopes


class ScopeClaims:
    """Weighted turns, skipping empty/cooldown lanes without spending attempts.

    Each probe still uses the original fenced SQL claim and its host filters.
    The cursor advances only on an owned job; an empty probe never reserves work.
    """
    def __init__(self, conn, scopes, claim, stopped=lambda: False):
        self.scopes = validate_scopes(scopes)
        for scope in self.scopes:
            if conn.execute("SELECT 1 FROM artifact_download_scopes WHERE scope_id=?", (scope,)).fetchone() is None:
                raise ValueError("Unknown artifact download scope: " + scope)
        self.turns = tuple(scope for scope in TURNS if scope in self.scopes)
        self.cursor = 0
        self.stopped = stopped
        self.claim = claim
        self.counts = {scope: 0 for scope in self.scopes}

    def __call__(self, conn, **kwargs):
        requested = tuple(kwargs.pop("artifact_types", ()))
        kwargs.pop("scope_id", None)
        probed = set()
        for offset in range(len(self.turns)):
            if self.stopped():
                return None
            index = (self.cursor + offset) % len(self.turns)
            scope = self.turns[index]
            if scope in probed:
                continue
            probed.add(scope)
            allowed = ELIGIBLE_SCOPES[scope]
            types = tuple(kind for kind in allowed if not requested or kind in requested)
            if not types:
                continue  # An empty SQL type filter means ALL: never pass it.
            job = self.claim(conn, scope_id=scope, artifact_types=types, **kwargs)
            if job is not None:
                self.cursor = (index + 1) % len(self.turns)
                self.counts[scope] += 1
                return job
        return None
