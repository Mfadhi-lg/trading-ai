# Periodic dispatch (offline candidate)

Explicit candidate entry: `python -m scrapper.commands.periodic_dispatch --apply`.
`--once --apply` admits one bounded batch per kind and executes at most one unit
per kind. Imports/help are inert. Apply reviewed075 explicitly before dispatch.
No current Compose definition changes. Activation requires a separately selected
rollout and a single ownership topology: stop/drain legacy artifact-reconciler
and rating-recovery-discovery before starting this role. Manual overlapping
reconciliation/recovery/backfill runs are prohibited.

Timer callbacks only enqueue singleton PENDING batches. Recovery starts at once;
full admission starts after30s. One-second polling coalesces missed ticks with
300s grace. No historical ticks accumulate: underlying due rows preserve backlog.
Delayed: budget50, next batch300s after settlement. Recovery: budget50, sleep3
when work exists /120 when idle. Housekeeping: budget40, cadence300s after
settlement, one accepted result and one credit-rating artifact per unit. Durable
result/artifact cursors sweep all records and wrap at exhaustion; normal acceptance
still immediately creates expectations and recovery targets. This replaces
unbounded startup housekeeping only in the opt-in role.

Each unit checks current exchange/company result priority. Delayed reconciliation
uses one expectation per unit and canonical shared Screener permits. This changes
old batch grouping: the same company/event may refresh separately for PPT and
transcript, preserving persisted outcomes at the cost of additional permitted
requests. Ready-result work pauses admission without spending a source retry.
Existing weekly schedules, expectation retry policies, source holds, host cooldowns,
recovery claims and outcomes remain authoritative. Recovery rotates explicit
BSE/NSE/issuer-IR sources, throttle2; it introduces no publisher bypass.

A local OS executor lock spans network work. DB transactions end before long work.
Admissions persist before work; crash consumes that slot, underlying claims remain
recoverable after expiry. Housekeeping cursors advance only after idempotent work.
SIGTERM drains current unit and prevents new admission. A failed unit records
FAILED/error and waits at least120s for another batch. Terminal task result stores
only the settlement unit, not aggregated prior outcomes; underlying ledgers remain
the full history. Concurrent timer ticks cannot duplicate PENDING batches.

Rollback before activation: omit overlay. After separately authorized activation,
stop/drain new owner; preserve075 migration/manifest, all inbox budgets/cursors and
existing queue/history rows. Old code requires075-compatible migration audit.
Resume one legacy ownership topology after accounting for pending work. Never drop
task history or restore an old database. No active-request preemption, distributed
locks or starvation guarantee. Single-host SQLite and local filesystem only.
