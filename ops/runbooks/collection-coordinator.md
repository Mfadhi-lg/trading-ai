# Collection coordinator (prepared offline)

Use `python -m scrapper.commands.collection_scope --serve --apply --limit 40`
only after a separately authorized release/migration and stopping the old
market-cap-monitor loop. Existing status/bootstrap/monitor CLI modes remain.
Compose is unchanged in C22.2. One scheduler and executor own each resolved DB
through local OS locks; no simultaneous manual monitor or old loop is allowed.

Schedule callbacks only enqueue. At most one pending batch exists. The next
batch is eligible 3600 seconds after settlement, preserving the prior sleep
cadence. Missed hours collapse into one batch; existing due rows carry backlog.
Each slot is durably admitted before company work. A crash may consume a slot
without completing a check; it never resets the batch budget. Scope leases and
weekly/error/defer dates remain authoritative, with shared host permits.
Current exchange tasks, due eligible company results and active company work
pause admission before the next company. This does not preempt an active request.
Continuous result traffic may defer collection; no production fairness claim.
No recovery, OCR, news, ML or trading work is registered.

SIGTERM/SIGINT stops admission and drains the current company. Failed batches
retain the error; pending and expired scope claims survive restart. Inspect
collection_scope_tasks alongside scope rows and acquisition host state. DONE
means batch settlement, not successful eligibility evidence; preserved monitor
results/errors remain honest. Admission totals include interrupted slots, while
source retry accounting remains in the original scope/host records.

Rollback: stop/drain the coordinator, retain 074 schema/manifest compatibility,
and restore the old monitor loop with one owner. Keep pending batch and all
scope/financial/content history; reconcile its consumed budget before resuming
coordinator dispatch. Pre-074 binaries reject the applied migration identity.
No schema drop, database restore or content relocation.
