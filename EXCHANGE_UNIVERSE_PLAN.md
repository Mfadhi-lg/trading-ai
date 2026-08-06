# Exchange Universe and Result-Discovery Layer

_Created: 2026-08-05_

## 1. Purpose

Replace screen-based universe discovery with the official exchange record. NSE
and BSE security masters decide which companies exist; official financial-result
filings decide which of them have reported a new period. Screener remains the
detailed company-page source and is never used for universe enumeration.

```text
NSE/BSE security masters
        ↓
durable company/security registry keyed primarily by ISIN
        ↓
NSE/BSE financial-result filings
        ↓
only new companies or advanced result periods enter the queue
        ↓
full Screener company-page collection (unchanged)
```

This removes the Screener pagination blocker recorded in
`SCREENER_INCREMENTAL_DISCOVERY_PLAN.md` §5 and discovers IPOs, delistings,
symbol changes, mergers and BSE-only companies as soon as they enter or leave an
exchange master.

## 2. Implemented surface

| Area | Path |
| --- | --- |
| Migration | `scrapper/db/migrations/032_exchange_universe.sql` |
| Delimited-source contract | `scrapper/sources/exchange/tabular.py` |
| Security masters | `scrapper/sources/exchange/security_master.py` |
| Symbol/name changes | `scrapper/sources/exchange/corporate_changes.py` |
| Result filings | `scrapper/sources/exchange/result_filings.py` |
| Registry, filings, queue bridge | `scrapper/db/exchange_repo.py` |
| Pipeline | `scrapper/pipeline/exchange_universe.py` |
| Coverage report | `scrapper/exports/exchange_universe_coverage.py` |
| Allowlisted fetcher | `scrapper/core/http.py` (`fetch_exchange_dataset`) |
| Tests | `scrapper/tests/test_e01_exchange_universe.py` |

Datasets: `nse_equity_master`, `nse_sme_master`, `bse_security_master`,
`nse_symbol_change`, `nse_name_change`, `nse_financial_results`,
`bse_financial_results`.

## 3. Identity rules

1. ISIN is the only union key. Its structure **and** ISO 6166 check digit are
   validated before it may link anything.
2. NSE symbol and BSE scrip code are stored in separate columns on separate
   security rows and are never merged into one identifier.
3. Two securities are never merged from name text, however similar the names.
4. A security whose ISIN is missing (typically a delisted or suspended BSE
   scrip) is retained and remains collectable by its own key, but joins no
   registry company.
5. A changed ISIN closes the previous registry link with an effective date and
   records an `isin_changed` event; the old link row is kept.
6. Symbol and name change files apply only when they resolve to an exchange key
   already in the registry. Unmatched changes are retained with
   `applied_status='unmatched'` and reported.

## 4. Effective dating and auditability

Every ingestion writes one dated snapshot (`exchange_source_snapshots`) bound to
the immutable response blob, the parser version and the source encoding.
Nothing is deleted:

- `exchange_security_versions` is append-only and effective-dated; a new version
  is written only when the normalized attribute tuple changes, and the previous
  version's `valid_to_date` is closed.
- `exchange_security_events` records `added`, `removed`, `reinstated`,
  `isin_changed`, `name_changed`, `status_changed`, `symbol_changed` and
  `attribute_changed`.
- Securities absent from a later full master are marked
  `is_present=0 / current_status='absent_from_master'`, never dropped.
- A snapshot older than a security's `last_observed_date` is stored as evidence
  but never moves current state backward.
- Every rejected source row is retained verbatim in
  `exchange_source_rejected_rows` with a reason code.

## 5. Incremental result discovery

`exchange_result_watermarks` stores the last broadcast instant per
exchange/dataset. Broadcast timestamps are published in IST and converted to
exact UTC instants; a date-only value is anchored at the start of its IST day so
the watermark can never skip filings published earlier that day.

Filings are keyed by
`exchange_key|period_end|reporting_scope|broadcast_at_utc`, so a revision with a
new broadcast time is a new filing while a repeated poll of the same feed stores
nothing new and creates no work.

Queue decisions per filing, all recorded in `exchange_screener_enqueues`:

| Decision | Meaning |
| --- | --- |
| `queued` | New company or advanced period; one durable queue item created |
| `reused` | Another exchange or screen already queued this company/scope/period |
| `already_accepted` | Screener has already accepted this exact period |
| `stale` | Filed period is older than accepted state; pointer never moves back |
| `manual_review` | Identity unresolved (e.g. symbol not yet in any master) |

## 6. Bridge to the Screener worker

Migration 032 rebuilds `screener_company_queue` so screen-discovered and
exchange-discovered work share one queue, one lease protocol and one event
history. `origin` is `screener_screen` or `exchange_filing`; exactly one of
`candidate_id` / `exchange_filing_id` is set. Existing rows migrate to
`screener_screen` with their candidate intact.

The Screener company path is derived, not guessed from names: Screener addresses
NSE-listed companies by NSE symbol and BSE-only companies by scrip code, so path
resolution walks the ISIN-keyed registry and prefers a current NSE equity symbol
before falling back to the BSE scrip code. A BSE filing for a dual-listed
company therefore collects the NSE company page, and both exchanges' filings for
one period collapse into a single queue item.

Reporting scope maps straight through: `consolidated` →
`/company/<KEY>/consolidated/`, `standalone` → `/company/<KEY>/`. The path is
validated by the same `normalize_company_path` contract the screen parser uses.

Acceptance is unchanged: the existing pipeline must parse the company page,
prove the expected period is present in quarterly results and pass every
validation gate. One policy change applies — when an exchange has published a
filing that Screener has not yet loaded, the period-absent failure is
**retryable** for `exchange_filing` origin and remains terminal for
`screener_screen` origin.

## 7. Acquisition policy

`--file` reads an operator-downloaded official file and performs no network
request. `--url` fetches through `fetch_exchange_dataset`, which permits only
HTTPS URLs on allowlisted NSE/BSE hosts and path prefixes, carries no Screener
session material, and rejects any response that is HTML, a captcha, an
access-denied body, or an unexpected media type. Both exchanges serve these
endpoints only to requests carrying their own public site as `Referer`/`Origin`,
which the fetcher now sends per exchange; it remains credential-free.

### Confirmed dataset URLs (verified 2026-08-05)

| Dataset | URL | Shape |
| --- | --- | --- |
| `nse_equity_master` | `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | CSV |
| `nse_sme_master` | `https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv` | CSV |
| `nse_symbol_change` | `https://nsearchives.nseindia.com/content/equities/symbolchange.csv` | headerless CSV |
| `nse_name_change` | `https://nsearchives.nseindia.com/content/equities/namechange.csv` | CSV (`NCH_*` headers) |
| `bse_security_master` | `https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active` | JSON |
| `nse_financial_results` | `https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly` | JSON |
| `bse_financial_results` | not yet confirmed — operator `--file` only | — |

The NSE result feed also accepts `&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`,
which filters on broadcast date within the feed's current period bucket. Its
CSV export (`&csv=true`) omits `SYMBOL` and therefore cannot be used: identity
must come from the JSON feed.

Reference pages for each dataset:

- NSE securities available for trading — <https://www.nseindia.com/static/market-data/securities-available-for-trading>
- NSE all reports — <https://www.nseindia.com/all-reports>
- NSE financial results — <https://www.nseindia.com/companies-listing/corporate-filings-financial-results>
- BSE standardized security master specification and BSE corporate results

## 8. Commands

```bash
# Daily universe refresh (masters and change files).
python3 -m scrapper.cli exchange-refresh-universe \
  --dataset nse_equity_master --file /inbox/EQUITY_L.csv --effective-date 2026-08-05
python3 -m scrapper.cli exchange-refresh-universe \
  --dataset bse_security_master --url https://www.bseindia.com/downloads/...

# Incremental result discovery; queues only new or advanced periods.
python3 -m scrapper.cli exchange-discover-results \
  --dataset nse_financial_results --file /inbox/results.csv --dry-run
python3 -m scrapper.cli exchange-discover-results \
  --dataset nse_financial_results --file /inbox/results.csv

# The existing bounded worker collects the queued companies.
python3 -m scrapper.cli screener-company-worker --due --limit 5

# Coverage.
python3 -m scrapper.cli exchange-universe-coverage
python3 -m scrapper.cli screener-discovery-coverage
```

`--dry-run` performs no database, snapshot or queue write; result discovery
still projects its queue decisions read-only. Discovery exits non-zero when any
filing lands in manual review, and the coverage command exits non-zero while a
required master is missing, a snapshot failed validation, or review items exist.

## 9. Operating rules

- Refresh both masters daily, not quarterly.
- Union by ISIN; keep NSE symbol and BSE scrip code separate.
- Never merge companies from names alone.
- Preserve every dated snapshot; mark effective dates instead of deleting.
- Poll result filings incrementally by broadcast timestamp.
- Queue a company only when its reported period advances.
- Use filing metadata to select consolidated versus standalone scope.
- Keep Screener for the company-page dataset only.
- Clear manual-review filings by refreshing the masters before assuming a data
  problem: a newly listed symbol is expected to be unresolvable until the next
  master snapshot contains it.

## 10. Definition of done

Met by this change:

- official masters and result feeds parsed source-faithfully, header-bound, with
  every rejected row retained;
- ISIN check digits enforced before any union;
- additions, removals, renames, ISIN and status changes auditable per effective
  date;
- broadcast-watermarked incremental filing discovery that is idempotent across
  repeated polls;
- one durable queue item per company/scope/period across both exchanges and the
  screen;
- BSE-only, dual-listed and SME paths resolved safely;
- unresolvable identities routed to manual review instead of guessed;
- deterministic coverage reporting that separates discovery gaps from technical
  failures; and
- the complete offline suite passing (`python3 -m unittest discover -s scrapper/tests -t .`).

Still gated: the BSE result-filing feed URL is not yet confirmed, so
`bse_financial_results` remains an operator `--file` path. Every other dataset
URL is confirmed and automated (§7).

## 11. First production ingestion (2026-08-05)

| Dataset | Accepted | Rejected | Registry effect |
| --- | --- | --- | --- |
| `nse_equity_master` | 2395 | 0 | 2395 added, 2395 ISIN-linked |
| `nse_sme_master` | 560 | 0 | 560 added, 560 ISIN-linked |
| `bse_security_master` | 4940 | 2 (`invalid_isin`) | 4940 added, 4940 ISIN-linked |
| `nse_symbol_change` | 1043 | 10 | 401 applied, 642 unmatched |
| `nse_name_change` | 2319 | 1 | 546 applied, 280 reused, 1493 unmatched |
| `nse_financial_results` | 3813 | 3 | 3564 queued, 119 reused, 130 manual review |

Resulting universe: 7895 present securities, 5629 registry companies, 2266
dual-listed. A repeated result poll stored 0 filings and queued 0 items,
confirming §5 idempotency against the live feed.

The unmatched change rows are expected: they are historical renames of symbols
no longer in any current master. The 130 manual-review filings are symbols
absent from the masters (per §9, recheck after the next master refresh).

The live NSE result feed currently publishes `2024-12` as its most recent
quarterly bucket for every parameter combination tried, so discovery pointers
advance only to that period. Collection itself is unaffected — a Screener
company page carries all quarters — but the queue reflects the exchange record,
not the newest quarter Screener may hold.
