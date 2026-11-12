# SS-02 full existing-universe business mapping

The destination remains `artifacts/subsector-ss01/v1/research.sqlite3`. Only `ss02_*`
tables/views are added. Existing canonical IDs, source classifications, memberships
and every mentor assessment are unchanged. `ss01-v2-before.sqlite3` is an audit
backup, not a competing working database.

All 4,428 existing canonical companies and all 5,918 existing current ISINs have
mapping or review rows. Granular assignment is **not complete**. See
`artifacts/subsector-ss02/CLASSIFICATION_RECONCILIATION.md` for verified, proposed,
diversified and unresolved counts. An inventory row or a broad industry descriptor
does not count as a verified business subsector.

The earlier peer-discovery draft is superseded. No external companies are appended
to the master. Official metadata enriches only existing canonical IDs. All company
candidate sets come from those existing IDs; the historical reports are evidence,
not a replacement universe.

## Reproduction with frozen evidence

```sh
python3 -m scrapper.research.subsector_ss02.full_universe plan
python3 -m scrapper.research.subsector_ss02.full_universe apply
python3 -m scrapper.research.subsector_ss02.reconcile
python3 -m unittest scrapper.tests.test_subsector_ss01 scrapper.tests.test_subsector_ss01_q2 scrapper.tests.test_subsector_ss02 -v
```

The apply command requires an exact saved plan, the isolated research-purpose marker,
matching original-table fingerprint and fixed SS-02 migration. It refuses to silently
replace an already frozen run with a different plan. Future classification revisions
require an explicitly versioned run/migration; original teacher evidence must remain
immutable. Canonical-company creation for unmatched securities is deliberately not
performed by this classification importer.

The original absolute source paths are part of the audit identity. Frozen reruns are
deterministic at the current repository location. A relocated review ZIP supports
read-only SQLite/CSV review; do not rewrite source IDs to force a replay.

## Evidence collection already completed

`prepare_inputs` and `inventory` read the archived master with SQLite `mode=ro`.
They retain exact official symbol/ISIN/legal-name links and all existing business
descriptions, original observation/parse/blob references, source URLs and hashes.
`enrich_business_metadata` used existing BSE identifiers only, with bounded anonymous
requests, a four-request/second ceiling, verified TLS, caching and refusal limits.
It retained 4,186 responses and rejected three identity mismatches.

No network is needed for the frozen replay. Do not rerun enrichment as part of a
frozen apply: new current evidence needs a new explicit research version. Current
market caps, ratios and other numeric header values are not classification or
historical selection inputs. Original JSON values and source-response digests are
retained in wrappers; byte-exact raw JSON serialization is not separately stored.

`taxonomy.py` defines proposed granular product/service functions. Generic Auto,
Chemicals, Pharma, Electronics, Capital Goods, NBFC and unspecified "Other" groups
are insufficient. Only explicit fine official descriptors enter the verified
crosswalk. Literal description matches remain reviewable proposals; no company is
classified from a similar name alone. Multiple activities remain separately evidenced,
with no forced primary when dominance is unknown. Unresolved status can reflect
missing detail **or an unimplemented taxonomy/recognition case**, not necessarily
the absence of public evidence.

Original tutor labels and aliases remain separate source facts. The new analytical
codes do not rewrite those labels, source dates or quarter-specific decisions.
Primary-source notes retain preliminary anchor labels for context; those anchors
are not the granular outputs. Read `primary_code`, `status`, role and provenance.

## Historical baskets

Historical qualified rows require contemporaneous fine business metadata, positive
capitalisation, published impact cost and a quote-presence record from the retained
NSE reports. Q4 uses February; Q1 uses April. The May file revised after June is
retained as rejected/context evidence. Ranking ignores monthly returns, current
values and future performance. The source index-cap field is kept under its exact
meaning. Weights are initial basket metadata; no index prices are calculated.

All manifests are retrospective research freezes and `chart_ready=0`. Candidate-only
manifests state missing evidence; they do not assert historical ineligibility. Even
qualified rows do not certify complete historical feed coverage, absence of later
suspensions or unbiased historical-universe coverage. Publisher modification times
are attestations, not independent web archives. Current business observations are
never backdated into March/June knowledge. `queries.sql` compares instants using
SQLite `julianday`, including timezone offsets.

No candles, final charts, Document Intelligence pipeline, ML or production changes
are part of this module. [Basket methodology and calculation boundary](INDEX_METHODOLOGY.md)
documents historical eligibility, initial weights, and the evidence still required
for index levels or benchmark comparisons.

Tested with Python 3.14, SQLite, openpyxl 3.1.5, pypdf 6.14.2 and certifi 2026.7.22.
The migration is outside the scraper's runtime migration directory.
