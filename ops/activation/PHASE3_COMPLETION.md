# Phase 3 — COMPLETE

Completed 11 October 2026. The installed scheduler has actually executed the new policy; this is operational acceptance, not readiness-only status. [Machine-readable verification](../../artifacts/phase3-completion/verification.json).

## Permanent scheduling

Financial processing runs daily at HH:15 and HH:45 IST, at most two companies per slot and a 600-second deadline. The retained job name `current-financial` protects existing slot identities; its selection is now `three-priority-v1`.

- P1: latest completed reporting quarter calculated using IST; source metadata supplies the filing's actual reporting identity. Quarter and financial-year rollover require no manual quarter edit.
- P2: newly discovered older-quarter filings and source revisions. The discovery epoch is 9 October 2026 UTC; immutable source identity/discovery links distinguish new work from the preexisting backlog. Revision refreshes preserve previously accepted observations, parse pointers and source lineage.
- P3: one historical company within the two-company total at 00:45, 06:45, 12:45 and 18:45 IST, at most four per day. No unused live capacity becomes additional historical capacity. Eligibility, disposition, retries and source permits still apply.

The first position serves P1, falling back to P2. The second reserves P3 at its four slots when due eligible historical work exists; otherwise it serves P2 before another P1. This gives late/revised filings capacity under continuous current-quarter demand and historical work a finite reservation. No second queue or processing pipeline was created.

Shared host/per-DB ownership, canonical deduplication, fenced claims, recovery, rate limits, controlled retry backoff, authenticated retrieval and acceptance validation remain active. Required quick-ratio content is checked beyond HTTP status. Login/access failures stop the affected lane without accepting incomplete financials; genuinely unavailable premium commentary remains optional. Verified existing source aliases are used without guessing company identities.

## Actual production evidence

The installed Aqua LaunchAgent still ticks every 60 seconds with RunAtLoad. Its installed plist matches the repository configuration, launchctl reports the registered agent, and all declared runtime hashes pass. [Installed verification](../../artifacts/phase3-completion/launchctl-final.txt). These four runs came from launchd, not simulated clock calls:

- 18:15: STEAMHOUSE 18128 / parse34427 and LCCPROJECT 18129 / parse34429 accepted.
- 18:45: VEEGALAND 18130 / parse34431 and GLASSWALL 18131 / parse34433 accepted.
- 19:15: KANOHAR 18132 / parse34435 and KARAMTARA 18133 / parse34437 accepted.
- 19:45: RENTOMOJO 18134 / parse34439 and ARCIL 18135 / parse34441 accepted.

All eight are fresh June-quarter work, proving older-quarter filings are no longer excluded. Claims settled successfully. Their receipts and selections are linked in the machine-readable verification. Retained September GMBREW, TCS, ONIXSOLAR and LOTUSCHO acceptance evidence was reused without reprocessing those items.

The actual 18:45 reservation ran with historical budget one. No due eligible P3 work existed, so it processed two P2 items. Historical claims are therefore zero in this observation window; no claim is made that a production historical item was processed. Positive fixtures verify historical selection, daily bounds and isolation when eligible work exists. Existing eligibility/disposition/backoff holds remain respected.

BSE's actual 18:00 capture returned valid March-quarter source results despite a September query hint. The parser's query-period filter was corrected so source reporting metadata remains authoritative, matching NSE behavior. A separately identified reassessment reused the exact retained response with zero additional source requests: snapshot271, fetch78827, blob144562; two source filings persisted, one canonical queue created and one reused. Original receipt/raw bytes and BSE API holds remain intact. [Reassessment proof](../../artifacts/phase3-completion/bse-retained-reassessment/verification.json). Revisions are handed to the canonical financial refresh path; unresolved identity ambiguity retains its stop protection.

Focused validation: **166 passed, 12 Linux-only host checks skipped on macOS, 22 subtests passed**. Existing Linux evidence and actual production safety preflights were retained. Coverage includes rollover, late/revised filings, duplicate canonical claims, retries/rollback, accepted history, reserved catch-up fairness, official provenance attachment, inclusive580 eligibility/re-entry and retained BSE source input. [Test log](../../artifacts/phase3-completion/focused-tests.log).

## Independent market-cap monitoring

HH:00 IST, excluding 09:00 and 18:00; max 40 checks/slot, max 880/day, deadline 600 seconds. Oldest due scope first; financial backlog does not gate admission. Missed slots are not replayed.

The actual 19:00 run checked 40 companies and refreshed 39. KAKATCEM's expired lease recovered and released normally; cap77 crore correctly remains below the floor. Code512265 returned missing/non-numeric cap, retained previous information and received controlled backoff. Accepted financial versions were unchanged. [Actual batch](../../artifacts/automatic-acquisition/2026-10-11-market-cap-1900/runtime.log).

Read-only production snapshot at **19:35:04 IST, 11 October**: **5,240 overdue checks**, compared with the old 4,366 reference. Since9October, receipts/database account for 128 completed checks,124 valid observations across 124 distinct companies and four failed checks in that interval (including retained partial-run outcomes). Current backoff state is 55 rows with 64 cumulative failures; these are not 64 new failures in this phase. This completion session's focused HEG check plus 19:00 batch account for 41 checks and 40 valid refreshes. [Exact snapshot](../../artifacts/phase3-completion/final-inspection/production-inspection.json).

Recorded company re-entry and confirmed queue eligibility-revival events are both zero. Earlier monitor records did not persist previous eligibility on every check, so an exact retrospective newly-eligible total cannot be asserted. Forward observations now retain previous eligibility/cap/timestamp and explicit became-eligible/re-entry flags. Inclusive **₹580 crore** eligibility and automatic below-floor recovery are tested; accepted historical data is never deleted. Healthy bounded catch-up continues automatically and does not block Phase 3 completion.

## HEG resolution

The company mapping already verified source company id 1301 and current URL `https://www.screener.in/company/HEGAM/consolidated/`. The monitor had constructed the stale logical HEG path instead of using that verified source identity. It now requests `/company/HEGAM/` while preserving the canonical `/company/HEG/` scope and history. One focused authenticated request accepted **₹7,703 crore**; fetch78818/blob144555. Previous accepted parse 725 and its₹13,217 crore cap remain intact. Backoff cleared; next check follows the normal interval. Other company mappings remain governed by the same verified identifiers. [Focused outcome](../../artifacts/phase3-completion/heg-correction/outcome.json).

## Nonblocking monitoring and evidence limits

The desktop must remain logged in/awake and Docker/session access available for this existing Aqua scheduler. Missed slots are intentionally not replayed. Monitor session expiry, queue backoff, code 512265's absent cap and increasing overdue counts while bounded refresh continues. No lane stop latch remained at final verification.

A legacy fixed-output inspection invocation overwrote older preparation-time inspection outputs before failing to open read-only SQLite. It did not mutate production. The lost preparation snapshot is explicitly recorded; accepted canary receipts, database history and backups remain. The new inspection driver refuses output reuse and verifies empty WAL/no locks before immutable read-only access. [Incident](../../artifacts/phase3-completion/legacy-inspection-failure/incident.json).

## Exact Phase 4 handoff

Next: **automatic document discovery, downloading and processing**. Reuse the existing eligible-company/downstream state, document queues/adapters, immutable archive, provenance and ownership contracts. Before document writes verify external-drive presence/identity/sentinel and fresh physical capacity reserves. Prove a bounded discovery → download → immutable archive → processing handoff with one owner per queue. Preserve BSE API holds, database schema075, financial history, canonical identities, Docker mounts and source restrictions. Do not rebuild financial acquisition, repeat migrations or launch unrestricted company/document/rating/extraction workers. Phase 3 schedules continue independently during Phase 4.
