# Reserved future-domain contract — C25.4

Contract revision: `future-domain-contract-v1` (documentation, not a runtime API).
Status: reserved; F01–F05 remain DEFERRED. This contract describes the boundary
that future separately authorized implementations must satisfy. It creates no
packages, schemas, workers, providers, models, credentials or trading execution.
Changing a proposed payload requires a reviewed contract revision; none of the
proposed fields below are claims about existing persistence.

## Existing inputs and their guarantees

Financial consumers use `scrapper.research.features.company_quarter_features`
(`feature_version='v1'`) with a caller-owned read-only SQLite connection,
company ID, offset-aware decision time, explicit standalone scope, and optional
period/source disambiguation. The public boundary returns
`CompanyQuarterFeatures`: identity, period, decision time, scope, feature version,
status, completeness, reasons, provenance, features, trends and supplemental
observations. Each `QuarterFeature` retains feature code, decimal string or NULL,
status, unit, scale multiplier, period/type, availability time, input lineage and
formula. Preserve these values without silently imputing, changing scale or
mixing consolidated and standalone records. Ambiguous source versions and
invalid inputs fail closed. See the [financial interface](../../scrapper/research/features/README.md).

`PIT_SAFE_FROM_CAPTURE` describes stored capture/acceptance/observation and
revision eligibility, not historical filing-time availability, universe or
sector governance. `EXPLORATORY_BACKFILL` stays exploratory. The retained B1
29-row/464-value cohort has PARTIAL original DB/source-byte reconciliation;
engineering completeness is not a selection decision or training certification.
Legacy `scrapper.pipeline.ml_contracts` remains compatible; future consumers use
the public research facade and do not duplicate its acquisition SQL.

Document consumers use
`scrapper.research.features.documents.document_evidence` (`document-evidence-v1`)
with a caller-owned read-only connection, positive expectation ID and offset-aware
`as_of_utc`. `DocumentEvidence` retains version/cutoff, identity, expectation/check
IDs, observation/availability times, recorded result, exact evidence/error JSON
and availability basis. Content/version/hash/origin and extraction references
are carried only when recorded, never synthesized. `recorded_ready` is True for
recorded available evidence and otherwise NULL. Missing checks have NULL result;
unknown or not-yet-recorded expectations raise KeyError. See the
[document interface](../../scrapper/research/features/DOCUMENT_EVIDENCE.md).

Preserve the exact D09 states: `available`, `expected_not_yet_published`,
`not_offered`, `not_applicable`, `no_rows_reported`, `source_not_covered`,
`inaccessible`, `discovery_failed`, `unknown`. Do not collapse them into zero,
negative labels or a claim of usable extraction. Optional document absence does
not invalidate accepted financial results. Readiness is not an issuer/event
correctness certificate. Deterministic extraction remains owned by
`documents/extraction`; readiness does not itself return a text/page corpus.
Future claim producers need explicitly selected existing extraction records and
immutable content references, with a separately reviewed extraction adapter if
needed, rather than SQL against acquisition tables inside an AI consumer.

D09 has producer-supplied check times without independent ingestion/commit time
or immutable content/identity snapshots per check. Recorded-as-of replay cannot
prove backfilled evidence existed at a historical cutoff or reconstruct edited
identity rows. `available_at_utc` is the selected available check time, not
publication or initial acceptance time. Consumers must carry
`recorded_check_timestamp_not_historical_PIT_certification`; retrospective use
requires separately established provenance, never relabeling the existing API.

Existing Focus ownership is `research.governance.focus_p0` and
`research.focus.build_p0_governance`, with old facades retained. Preserve approved
universe/label/archetype sources, hashes, versions, fixed selection seed, cutoff
and incomplete-pool refusal. Its broadcast-filtered query/current identity facts
do not prove historical PIT eligibility. Zero requires governed negative
membership; unknown, technical failure and ambiguity remain NULL/quarantined.
B2 threshold/freeze material is SUPERSEDED_PROVISIONAL; incomplete draft selectors
are audit-only under the retained supersession record. Do not import them or turn
absence from the positive list into mentor rejection. See the
[governance limits](../../scrapper/research/governance/README.md).

## Shared proposed evidence envelope

Future outputs must have a stable output ID, contract/schema version, producer
and run ID, issuer/entity or instrument identity, source/content references and
hashes, input snapshot hash, requested cutoff, produced/first-recorded times,
availability basis, verification/review status and supersedes/correction links.
Unknown identity/time/value stays explicit. Publication, event, observation,
ingestion, production and review times have distinct meanings; use offset-aware
timestamps normalized to UTC for comparisons, retaining original source timezone.
Units/currency/scale and applicability travel with values. Evidence and decisions
are versioned and append-only in intent; corrections supersede without rewriting
the snapshot that a prior decision consumed.

A source published before a cutoff but first captured after it cannot be used as
known-at-cutoff input. A corrected claim reviewed tomorrow cannot become today's
approved feature. Downstream availability must account for actual input capture
and required validation/review, rather than merely the oldest publication date.
Missing historical proof produces unknown or a named retrospective basis and is
excluded from datasets requiring historical availability. No consumer upgrades
the weaker document/governance guarantee to the financial capture guarantee.

Future research and trading consume versioned snapshots through explicit public
interfaces. Grouped repositories own storage SQL; provider adapters own fetching;
workflows own acquisition. No future consumer is required to import acquisition
tables, parsers, CLI, view or operational schedulers. Adapters may use existing
canonical storage behind those boundaries; there is one financial store, logical
document model and immutable content store. No second downloader or OCR corpus.

## ML datasets, training and inference — `research/ml`

Input: financial/document projections plus separately approved contextual
features, a frozen eligible universe, explicit sector/applicability facts, dated
labels and their provenance/approval, feature/label/universe versions and cutoff
policy. Snapshot manifests retain every input ID/hash and exclusion reason.
Positive evidence, governed negatives, unknowns and failures stay distinct.
Historical population membership is required for a historical eligibility claim.
Current listings or a retained positive reference do not reconstruct it.

Output: versioned dataset manifest, split/evaluation specification, training run
and model artifact hashes, configuration/seed/dependency versions and metrics;
inference returns a versioned research decision with model/feature versions,
input snapshot, cutoff/availability basis, score/meaning, uncertainty and review
status. A score is not an approved order. Deterministic approved Focus can use
the same features independently of ML and must not wait for model training.

Later acceptance requires dated label/universe evidence, leakage checks for
revisions and availability, appropriate temporal/entity splits, reproducible
replay and a reviewed evaluation/promotion gate. The cleanup does not establish
these prerequisites. Dataset/model artifacts use configured future locations;
no training, automatic label generation, model registry or worker runs now.

## Document AI claims — `intelligence/documents`

Input: explicit selected logical artifacts, immutable original content IDs/hashes,
original versus generated/derivative origin, deterministic extraction version,
page/slide/segment text and spans, issuer/event/period identity, and recorded
evidence with its availability limitations. Reuse extraction/OCR; resolve the
same logical artifact after recovery, rather than treating replacement bytes as
a new issuer event. The readiness interface alone is insufficient text input.

Output: structured claims retaining value/unit/qualifier, issuer and period,
content hash/ID, extraction version, page/slide/span citation, model/prompt/schema
versions, confidence, availability and verification/review status. Unsupported
claims remain unknown or awaiting review. A citation must resolve to the exact
selected bytes and extraction; confidence is not verification. Accepted financial
facts and original documents are never overwritten by model text.

Claim validation/reconciliation and approved feature promotion are separate
steps. Cache by content + extraction/options + model/prompt/schema versions;
correction/review versions remain replayable. A future
`document-intelligence-worker` is independent of deterministic `extraction-worker`,
with bounded selection, cost, retries and idempotent outputs. Later acceptance
needs a cited sample, human validation, error/cost evidence and review semantics.
No prompt, LLM call, bulk extraction or feature promotion is implemented here.

## News evidence — `sources/news` and `intelligence/news`

Input: separately approved publisher/feed scope and permissions through source
adapters, with immutable captured evidence. Retain publisher/source reference,
publication time, first-seen/ingestion time, event time if known, entity matching
basis and correction/version history. Unresolved entity links stay ambiguous;
deduplication must retain original source references.

Output: versioned cited event/context records, resolved or unresolved entities,
relevance, summaries/claims, freshness, cutoff and review status under the shared
envelope. A later article or correction never enters an earlier decision.
News does not replace accepted financial facts or submit orders. Later acceptance
requires licensed/approved source selection, entity and correction tests, cutoff
replay and freshness policy. `news-research-worker` is a reserved role only;
there is no feed/provider integration, subscription or agent implementation.

## Market/session/futures context — `sources/markets` and `intelligence/markets`

Input: approved provider observations with venue and stable instrument ID,
spot/futures designation, contract/expiry where relevant, price field, unit,
currency, exchange timezone, provider delay/entitlement, observation/source and
receipt times, and versioned session/calendar definitions. Do not infer these
from news prose or join symbols without instrument/contract identity.

Output: versioned US/global context, session state, quote/freshness and cross-market
events linked to exact observations. Distinguish premarket, regular, overnight,
closed-session, delayed/stale and expired-contract values; retain uncertainty
rather than inventing a current quote. News and markets may share evidence
envelopes while retaining separate tasks and source policies. Later acceptance
requires provider/calendar/entitlement selection, timezone/DST/holiday and roll
tests, timestamp replay and freshness refusal. `market-context-worker` remains
reserved; no provider, calendar service or market runner is installed.

## Approved signals and trading — `trading`

Input: explicitly approved versioned signals referencing research decisions,
feature/context snapshots, issuer-to-instrument mapping, cutoff, validity window,
strategy/version, approval and execution environment. Free-form AI text, raw news
and a model score cannot be submitted as order intent. Unknown/stale context,
unresolved instruments or absent approvals fail closed under the later policy.

Output chain: signal validation → portfolio/risk decision → auditable order intent
→ execution acknowledgement/rejection → fills/positions → reconciliation. Each
transition retains stable IDs, input/risk-policy versions, environment, timestamps,
idempotency/correlation keys and status/rejection reasons. Partial fills, cancelled
orders and uncertain broker outcomes require reconciliation; retries cannot
blindly duplicate submission. Only a separately authorized trading execution
adapter may submit orders, after approved portfolio/risk checks. Trading must not
scrape or invoke parsers during submission.

Later acceptance requires broker/environment selection, risk and position limits,
simulation/paper verification, cancellation/restart/duplicate/outcome tests,
reconciliation and explicit activation scope. Paper and live environments need
separate identities/credentials and authorization. No credentials, broker API,
paper/live runner or order action is introduced. Suggested signals/risk/orders/
execution/reconciliation/portfolio modules are names reserved for real future work.

## Scheduling, persistence and implementation gates

Coordinator schedules bounded future tasks; long AI/training jobs do not block
financial ingestion or deterministic extraction. Process/container isolation is
chosen from actual load/latency later, not from a fixed role count. New durable
work contracts need separately reviewed ownership, idempotency, retries, budget,
crash/restart and migration evidence before scheduling is enabled.
Configured `intelligence/`, `datasets/`, `models/` and `trading/` artifact locations
are reserved; canonical data mounts and stored paths remain unchanged. Grouped
`db/research`, `db/intelligence`, `db/trading` ownership is future scope, not new
tables or a second system.

F01 document claims, F02 ML, F03 news, F04 market context and F05 trading each need
their own selected implementation and release gates. Cleanup completion does
not authorize them. C27.1 freezes the accepted cumulative source and separate
data/artifact retention manifests; C28.1 alone promotes the frozen source to
canonical main. C25.4 rollback omits this document and retains the C25.3 candidate;
there is no database, queue, content or runtime rollback action.
