# Direct-rating instances

Use `run-credit-rating-download-worker --rating-instance care|india-ratings|other`
with the existing bounded flags. Each instance uses the same RatingWorker class,
explicit historical target-host partition and mandatory C20 shared permits.
`--host` remains available for legacy explicit operator selection; it cannot be
combined with `--rating-instance`. Omitting both preserves the legacy API.
Reviewed publisher restrictions, cross-host redirect permits, claim fencing,
renewal, attempts and cooldown remain enforced by the existing executor.

Keep three independent processes, all existing mounts/resources/identities,
the reserved accepted generic process, 25-job batches, four-second throttle and
ten-second shell cadence. Shell lifecycle replacement belongs to C23.4.

This candidate is offline. Rebuild and verify the frozen cumulative release
before separately authorized rollout. Drain owners before switching topology.
Rollback restores the prior role commands/image while retaining all queue,
permit, attempt and content history. An unmetered legacy fallback requires
stopping overlapping publisher executors or a verified shared-permit entrypoint.
