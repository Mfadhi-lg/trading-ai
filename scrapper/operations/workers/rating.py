"""Host-configured bounded direct-rating instances; one existing queue owner."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RatingWorkerConfig:
    name: str
    hosts: tuple[str, ...]


INSTANCES = {
    "care": RatingWorkerConfig("care", ("www.careratings.com",)),
    "india-ratings": RatingWorkerConfig("india-ratings", ("www.indiaratings.co.in",)),
    "other": RatingWorkerConfig("other", (
        "www.infomerics.com", "www.acuite.in", "connect.acuite.in",
        "bcrisp.in", "www.bcrisp.in", "www.brickworkratings.com",
        "crisil.com", "www.crisil.com", "crisilratings.com",
        "www.crisilratings.com", "icra.in", "www.icra.in",
    )),
}


class RatingWorker:
    """Reuse fenced claims, resolver, durable permits and bounded lifecycle."""

    def __init__(self, instance: str):
        try:
            self.config = INSTANCES[instance]
        except (KeyError, TypeError):
            raise ValueError(f"Unknown rating instance: {instance!r}") from None

    def run(self, conn, **kwargs):
        # Never let an empty/overridden host selection authorize a broad drain.
        if "allowed_hosts" in kwargs or "shared_permits" in kwargs:
            raise ValueError("Rating instance owns host selection and shared permits")
        from scrapper.pipeline.credit_rating_downloads import run_credit_rating_download_worker
        return run_credit_rating_download_worker(
            conn, allowed_hosts=self.config.hosts, shared_permits=True, **kwargs
        )
