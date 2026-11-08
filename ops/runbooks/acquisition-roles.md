# Continuous acquisition role commands

Offline C23.4 candidate only; rebuild/verify the cumulative image and schema before a separately authorized activation. The configured C05.3 image is historical.

`run-acquisition-role --role NAME` replaces five proven shell loops, using the same bounded CLI handlers. Names remain document-download-worker, rating-recovery-download, credit-rating-worker-care, credit-rating-worker-india-ratings and credit-rating-worker-other. Generic roles retain limit25, throttle1s and post-batch2s waits. The accepted lane enqueues once on startup. Rating roles retain limit25, throttle4s, post-batch10s waits and C23.3 host partitions/shared permits. Each batch opens/closes its own connection and emits its existing report. Errors retain the prior non-set-e shell retry cadence and are explicitly logged. There are no broad-scope or cadence overrides.

SIGTERM/INT reaches Python directly and shares one lifecycle with the active bounded executor. Owned work drains/finalizes or releases through existing fencing; no next claim/batch starts after drain. Idle waits interrupt immediately and produce heartbeats. A hard kill still uses the existing lease/restart contract; no queue rows are deleted. Startup enqueue remains idempotent on restart. Health observes existing worker progress during work and WAITING during cadence delays.

Keep five independent services and resource isolation, including the recovery CPU cap. Restore the C23.3 commands/image after draining each new owner; do not overlap old/new roles. Queue/schema/content records require no rollback or migration.

Other loops are retained explicitly: daemon and market-cap-monitor lack the selected bounded lifecycle integration; artifact-reconciler and recovery-discovery have different startup/adaptive scheduling and prepared C22 owners requiring separate topology proof; June extraction is a dated campaign addressed in C23.5. No definition is removed or drained by this change. No exact container count is required.
