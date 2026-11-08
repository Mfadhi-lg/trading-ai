# Step 3 — current financial processing prepared, not activated

Production inspected read-only on 9 October 2026. No new source requests,
financial queue claims, company workers, downstream workers or historical
processing were started. Existing application images were reused unchanged.
No production migrations or completed release reviews were repeated.

## What works

The canonical path is connected:

1. Accepted NSE/BSE snapshots persist source-faithful filings and raw provenance.
2. The ISIN registry resolves a dual-listed company to its NSE Screener path and
   a BSE-only company to its scrip-code path. Filing reporting scope stays in raw
   provenance; collection deliberately uses one standalone company-quarter.
3. `exchange_screener_enqueues` creates/reuses the unique company/scope/period
   queue item. GMBREW and TCS each reuse one item across NSE/BSE and both filing
   scopes. Twelve September filing events produce five company queue items.
4. The company worker claims through existing fenced queue primitives. Its shared
   Linux transport controls permits, pacing, redirects, response bounds, source
   blocks, circuit state and lease renewal. The authenticated company page,
   required peer capture, quick ratios and optional commentary feed the existing
   raw-blob → parse → validation → acceptance → export pipeline.
5. The requested quarter must appear in source financial columns. New accepted
   versions add history and advance the financial and company collection state;
   older observations and acceptance events remain. The source page may include
   historical financial columns as part of that current snapshot; no older queue
   work is claimed by the prepared batch.
6. Canonical document/rating queue and expectation metadata is retained. Those
   consumers are not started, and this run downloads no documents.

The market-cap floor is **inclusive ₹580 crore**. Both ingestion and scope
recording exclude `<580`, allowing exactly `580`. Missing/non-numeric cap does
not prove eligibility. Below-floor failure preserves financial history and holds
work for a later cap check. The monitor refreshes source-grounded cap evidence,
verifies identity, clears only scope holds, records re-entry and requeues eligible
work. Its re-entry reason also prevents an old accepted-quarter watermark from
silently skipping the required refresh. Four production re-entry events exist,
the latest on 3 October. Boundary and re-entry tests pass.

## Current production findings

Five September standalone queue items are pending. Four are claimable:
GMBREW (18126), TCS (18127), ONIXSOLAR (18137) and LOTUSCHO (18138). Their last
recorded caps are ₹2,171 crore, ₹855,077 crore, ₹1,252 crore and ₹809 crore
respectively; these observations have different ages and are refreshed by the
future source run rather than treated as current prices. Ind Bank Housing
(18136, `/company/523465/`) remains excluded through its known below-floor alias,
even though the new queue row's own disposition is null. That alias guard works.

No September company financial results are accepted. GMBREW and TCS still point
to June. ONIXSOLAR and LOTUSCHO have verified existing company identities under
BSE-code aliases; null pointers on their newly discovered NSE-path collection
states do not mean new registry companies are required. All five current filing
registry bridges are populated.

The operative gap is that the company worker and cap monitor are inactive.
There are 5,490 scope rows: 1,887 eligible, 3,552 below floor and 51 unknown.
**4,366 checks are overdue**; the last monitor observations were on 4 October.
All accepted standalone companies have scope records, so a bootstrap is not
needed. The threshold/re-entry algorithms are present, but ongoing automatic
re-entry is not operational while the monitor remains off.

Two boundaries matter for activation:

- A global `screener-company-worker --due --limit N` has no current-quarter
  filter. Newer work sorts first, then older due rows can fill the same batch.
  The prepared adapter fences the existing claimant before any claims.
- The collection coordinator's `priority_ready` waits on *any* eligible due
  company row, including historical work. With a current-only financial worker,
  untouched historical queue rows could therefore indefinitely postpone cap
  monitoring. A focused fixture reproduces this. The prepared bounded cap-only
  entrypoint uses the existing monitor and shared transport directly, under the
  same per-operation ownership, without invoking that broad dispatcher.

One June alias pair (`/company/504092/` and `/company/INDOKEM/`) already has two
accepted queue records. It creates no active September duplication; preserve
both histories. Six other registry bridges remain null despite verified
identifier evidence. They are outside this canary and should be reconciled
selectively later, without a global bootstrap or name-based remapping.

## Shortest production route

The **prepared, not executed** first action is:

```sh
python3 ops/activation/step3_financial.py --execute
```

This processes only **18126/GMBREW and 18127/TCS**, September 2026, using the
existing canonical company worker and `SharedScreenerClient` in the existing
Linux image. Maximum two companies, 600-second deadline, existing 30-second
request timeout and 8 MiB per-response cap. It performs one invocation with no
automatic repeat. The existing shared acquisition lock, scheduler/executor OS
locks, owner/lease checks, production image/volume/file/schema/marker identity,
runtime target checks and WAL/SHM safeguards precede work. The private existing
session file is mounted read-only; its live validity remains untested.

The adapter requires the selected IDs to be the canonical next two due rows and
to remain current-quarter standalone exchange work. If order, quarter, scope or
ownership changes, it stops before claiming. It never selects older work to fill
the batch. The deadline bypasses the worker's per-company retry handler and
unwinds its existing cleanup. An `execution-started.json` receipt prevents a
second automatic/repeated invocation; preserve partial results and inspect any
failure before preparing another selected operation. Source publication lag,
auth rejection or validation failure remain honest failures/defer outcomes.

After real acceptance, verify September observations, parse/blob/fetch lineage,
accepted history and the two queue outcomes. Then prepare a fresh exact batch
for ONIXSOLAR and LOTUSCHO. Do not start the broad daemon or drain historical due
work. The separate **prepared, not executed or scheduled** cap-only check is:

```sh
python3 ops/activation/step3_financial.py --scope-monitor --execute
```

It calls the existing earliest-due cap monitor for **at most one scope row**,
with shared permits and the same host/DB ownership. It claims zero financial
company work, changes no accepted financial versions, and starts no consumers.
It is not restricted to one of the four September companies. Re-entry may make
existing queue metadata pending; current-only financial fencing still prevents
historical processing. A continuing monitor/current-only dispatcher should be
selected only after these bounded source behaviors are demonstrated. Keep all
sleep/cadence waits outside a production writer operation so NSE/BSE retain
their acquisition slots; do not run competing permanent writers.

## Verification and unattended exchange observation

**120 existing Linux tests passed**, plus **26 adapter/scheduling tests and six
subtests**. The selected financial production preflight passed with zero source
requests, zero queue claims and no company worker startup. The cap-only package
is prepared; its live execution has not been tested. Host shared-transport tests
correctly refused macOS because Linux parent-death fencing is required. Linux
fixture setup initially rejected inherited `/data` paths; explicit disposable
test paths resolved that without changing any guard or mounting production.

A read-only observer now runs after the existing acquisition tick. It examines
actual admissions, outcomes, runtime request/response logs, source counts, queue
decisions and BSE raw-response hashes. It distinguishes launchd-origin runs from
manual ticks, checks slot timing and existing acquisition bounds, and never
opens the production DB or launches a request. The report is
`artifacts/automatic-acquisition/first-unattended-verification.json`.

At preparation completion, both first real unattended attempts are still
pending. Next eligible NSE: **9 October, 19:30 IST**. Today's BSE legacy receipt
still consumes its access budget; first eligible BSE: **10 October, 09:00 IST**.
The observer will verify evidence automatically when those attempts occur and
record failures honestly. No extra attempts or receipt resets were forced.

Evidence: [Step 3 verification](../../artifacts/step3-financial-readiness/verification.json),
[read-only production snapshot](../../artifacts/step3-financial-readiness/production-inspection.json),
[identity and financial detail](../../artifacts/step3-financial-readiness/production-detail.json),
[financial plan](../../artifacts/step3-financial-readiness/prepared/plan.json),
[cap-only plan](../../artifacts/step3-financial-readiness/prepared-scope-monitor/plan.json).
