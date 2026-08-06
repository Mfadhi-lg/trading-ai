# Scrapper 2.0 — Exchange-Direct Events and Same-Day Metrics

_Status: future exploration; explicitly not in the current implementation scope._

_Created: 2026-08-04_

## 1. Why this is worth exploring

Direct NSE/BSE ingestion is the right long-term direction for time-sensitive
corporate disclosures. It can provide stronger provenance and earlier discovery
than waiting for an aggregator to index a filing. It should complement, not
immediately replace, Screener and Trendlyne:

- exchanges are authoritative for filings and exchange dissemination times;
- issuer investor-relations sites may still be the first or only host for some
  recordings and supporting material;
- Screener remains useful for normalized company history and cross-checking;
- Trendlyne or another licensed source may still be needed for fund-level holding
  data not present in an exchange announcement;
- the same filing may appear on both exchanges, be revised later, or have multiple
  attachments, so direct ingestion does not remove reconciliation work.

As of this document's review date, both exchanges advertise official corporate
data products. BSE describes API-delivered corporate financials, disclosures,
announcements and actions. NSE documents corporate-data products containing
fundamentals, announcements and shareholding data, including online and end-of-day
delivery options. Product access, pricing, licensing, redistribution rights and
technical delivery can change and must be confirmed directly before design or
procurement:

- BSE Self Data Feed: <https://marketdata.bseindia.com/>
- NSE Corporate Data: <https://www.nseindia.com/static/market-data/corporate-data-subscription>

Do not build a production dependency on undocumented website endpoints. First
evaluate the official feed/API/SFTP options and obtain permission for the intended
storage, processing and redistribution.

## 2. Proposed 2.0 objective

Build an event-driven, point-in-time-safe disclosure pipeline that receives new
NSE/BSE filings, resolves them to one company and event, preserves every original
attachment and revision, calculates versioned same-day research metrics, and
optionally alerts downstream users.

This is a research-data feature, not a promise of profitable trades. A metric that
looks useful anecdotally must be evaluated with fees, slippage, liquidity,
disclosure latency and strict no-lookahead timestamps before it can influence a
decision. Automatic order placement remains out of scope.

## 3. Candidate data

Start with corporate events whose source and timing can be audited:

- financial results and XBRL/iXBRL filings;
- board-meeting outcomes and result announcements;
- investor/earnings presentations;
- earnings-call schedules, recordings and official transcripts;
- material orders/contracts, cancellations and amendments;
- credit-rating actions;
- corporate actions;
- shareholding-pattern disclosures and pledges;
- management changes, resignations and other material Regulation 30 events.

Exchange-direct ingestion does not imply that every company has every artifact.
Reuse the availability states in `PLAN.md` so `expected_not_yet_published`,
`not_offered`, `not_applicable`, `source_not_covered` and fetch failure remain
distinct.

## 4. Candidate same-day metrics

Metrics should be deterministic and evidence-linked before adding any LLM-derived
features. Candidates include:

- reported revenue, EBITDA and PAT growth versus the comparable period;
- EBITDA/operating margin and quarter-on-quarter or year-on-year margin change;
- reported-versus-consensus surprise, only if a licensed point-in-time consensus
  dataset is available;
- order value relative to trailing revenue or market capitalization;
- credit-rating direction, outlook change and affected debt amount;
- promoter/institutional shareholding and pledge changes;
- guidance changes extracted with exact page/paragraph evidence and human review;
- event-to-market latency: exchange dissemination, first system observation,
  parsing completion and alert time;
- post-disclosure price/volume reaction for later evaluation, never backfilled as
  though it were known at filing time.

Every metric needs a versioned definition, unit, scope (standalone/consolidated),
period, source content hash, evidence location, `available_at_utc`, calculation
time and validation status. A revised filing creates a new metric version; it must
not rewrite what the system knew from the original filing.

## 5. Proposed architecture

```text
licensed NSE/BSE feed or permitted official endpoint
        ↓
immutable announcement envelope + attachments
        ↓
exchange/company/security identity resolution
        ↓
cross-exchange deduplication and revision linking
        ↓
deterministic event classification and document extraction
        ↓
versioned point-in-time metrics + evidence
        ↓
research alerts, replay and evaluation
```

Important controls:

- keep exchange dissemination time, exchange receipt time, first observation,
  download, parse and alert timestamps separate;
- map NSE symbol, BSE scrip code, ISIN and company identity with validity dates;
- preserve announcements from both exchanges even when their attachment bytes
  deduplicate;
- model corrections, withdrawals, clarifications and replacement attachments;
- quarantine identity conflicts, malformed XBRL and suspicious attachments;
- enforce feed entitlements, retention and redistribution terms in storage and
  exports;
- monitor completeness, latency, schema drift, sequence gaps and replay recovery;
- make alert delivery idempotent so retries cannot emit duplicate alerts.

The current Phase 2 cron/reconciliation worker remains useful in 2.0: exchange
announcements can create artifact expectations, and the worker can continue to
look for a recording or transcript that the issuer publishes hours or days later.

## 6. Research questions before implementation

1. Which official NSE and BSE product supplies the required events at the required
   latency, and through which protocol?
2. What are the current fees, licensing, retention and redistribution limits for
   internal research and any user-facing product?
3. Can the feed be replayed after downtime, and how are sequence gaps detected?
4. Which event types arrive as structured XBRL/iXBRL versus PDF or free text?
5. How are cross-exchange duplicates, revisions and dissemination timestamps
   represented?
6. What licensed point-in-time datasets are required for consensus, prices,
   market capitalization or fund ownership metrics?
7. What latency and precision are actually needed to add value after realistic
   trading costs and market reaction time?

## 7. Possible delivery stages

### R0 — Product and permission study

Compare official NSE/BSE offerings, obtain sample schemas and written usage terms,
estimate total cost, and decide whether low-latency access is justified.

### R1 — Historical announcement archive

Ingest a bounded company/event pilot with immutable attachments, company mapping,
cross-exchange deduplication, revisions and point-in-time timestamps.

### R2 — Near-real-time event ingestion

Add reliable polling/feed consumption, checkpointing, replay, sequence-gap
detection, schema-drift alarms and operational latency dashboards.

### R3 — Deterministic metrics

Implement a small metric registry with source evidence, versioned formulas and
golden fixtures. Start with financial-result and rating/order metrics.

### R4 — Research alerts and evaluation

Deliver paper/research alerts, then measure coverage, precision, latency and
forward returns on a genuinely point-in-time dataset. Define thresholds only from
train/research periods and keep an untouched evaluation period.

### R5 — Scope decision

Only after the evidence is strong should the project decide whether to expand the
universe, pay for lower-latency feeds, add reviewed qualitative metrics or connect
signals to a separate risk-controlled execution system.

## 8. Entry criteria

Scrapper 2.0 should not enter implementation until the current scraper has stable
company identifiers, immutable artifacts, explicit availability states and the
scheduled artifact reconciliation described in `PLAN.md`. It also needs an
approved official data-access route, a bounded pilot universe, a metric shortlist
and an evaluation protocol that prevents lookahead bias.
