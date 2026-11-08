# Exchange coordinator — C22.1 offline candidate

The existing `exchange-scheduler` CLI and Compose role name remain the operator
entry. `--once`, `--attempt`, `--status`, `--weekly-report`, `--catch-up`,
`--catch-up-days`, and source-access resume/evidence flags retain their existing
contracts. Serving the clock now starts one scheduler and one separate exchange
executor. No recovery, document download, OCR, ML, news or trading task is
registered. Production deployment has not been performed by this chunk.

## Dispatch and accounting

Cron IDs, IST triggers, `mon-sat` retry rungs (19:30, 21:00, next-day 06:00),
Sunday report, seven-day misfire grace and coalescing are unchanged. A callback
only records one task. Startup catch-up selects the same bounded missing days,
then prints the existing catch-up envelope with `queued_task_ids` and empty
`attempts`; actual attempt JSON appears asynchronously as the executor finishes.
This is an intentional change to serving output/order. The explicit manual
`--catch-up` mode remains synchronous.

`exchange_coordinator_tasks` retains the dispatch key, priority, state,
owner/generation/lease, result JSON, exception and completion time. A completed
callback is deduplicated across restart. DONE means the workflow result was
accounted; `result_json.status` may still be incomplete or access_blocked.
FAILED records an unexpected exception and an alert; it is not success or an
automatic source retry. Later cron rungs continue the existing publication retry
ladder. Do not reset a DONE/FAILED row to manufacture additional network calls.
Unexpected failures need a separately reviewed diagnosis/resume action.

Current scheduled attempts run before queued catch-up and weekly reports. A
running attempt finishes without preemption. Dataset dependency order remains
master, corporate change, result filing; current/previous quarter revision rules,
BSE holds, guards, result watermarks and existing Screener enqueue semantics are
unchanged. This independent executor does not share a scheduling thread or an
OCR/recovery work lane. This chunk makes no deployed latency/throughput claim.

For read-only dispatch diagnosis, use a read-only SQLite connection against the
configured database and inspect:

```sql
SELECT task_id,kind,scheduled_for,attempt,priority,state,generation,
       lease_until_utc,finished_at_utc,error,result_json
FROM exchange_coordinator_tasks ORDER BY task_id;
```

The legacy `--status` output remains the exchange publication ledger/status;
it does not imply a pending inbox is drained. Preserve both evidence surfaces.

## Ownership, restart and drain

Two advisory lock files next to the resolved configured DB enforce one serve
owner and one exchange executor. The executor holds its OS lock from claim
through workflow and completion, with no open DB transaction during network or
report work. Lease expiry cannot overlap an active lock holder. A killed owner
releases the lock; its RUNNING task becomes recoverable when its persisted
lease expires. A new generation prevents a stale finish. A crash after durable
workflow publication can replay; existing ledgers/watermarks/immutable content
keep durable outcomes idempotent. Revision fetches may repeat after a crash;
there is no exactly-once network guarantee.

These locks require the current single-host, local-filesystem SQLite topology.
They are not a distributed locking design. Legacy/manual acquisition does not
participate in these locks: only one scheduling implementation may own the role.
SIGTERM/SIGINT stops admission, finishes the current bounded attempt/report,
then joins the executor. Grace must cover existing source timeouts/report work;
forced termination instead uses lease/lock recovery. Lock files are runtime
state, contain no credentials and may remain after graceful exit; the kernel
lock, not file presence, determines ownership. Do not delete a live lock file.

## Later activation and rollback

Before a separately authorized rollout, stop the prior scheduler, capture the
consistent clone/pending-work baseline, verify runtime identities, and explicitly
apply additive `073_exchange_coordinator_tasks.sql` with the reviewed manifest.
No automatic migration is performed by serving. Missing migration or an unsafe
writable target refuses startup before a lock or source request. Compose/image
promotion, canary selection/observation and production migration are outside this
chunk. Do not change all role definitions as a prerequisite to deploying this
one role later.

Rollback is a code/topology change with one scheduler owner. Drain/stop the new
executor first; retain every new task row and all acquisition history. Restore
prior scheduling only in a build that retains migration 073 and its manifest
compatibility, without running the new dispatcher. The unmodified pre-073 image
rejects the extra applied migration identity and is not a safe rollback image.
A forward rollback can restore the pre-C22.1 scheduler implementation and CLI
routing while keeping 073 SQL/manifest available. No schema drop, ledger-row
removal or old database restore is a routine rollback. Reconcile unfinished task
keys against the existing attempt ledger before resuming the new inbox later;
never run both scheduling implementations to drain it.

C22.2 collection scheduling and C22.3 delayed/recovery/housekeeping dispatch
remain separate unimplemented chunks. C23 role consolidation remains separate.
