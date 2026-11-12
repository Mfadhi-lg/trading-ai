# Experiment lifecycle

This index originated with issues #20 and #25 against source at `c0025bac6`,
inspected 12 November 2026. The same-day daily-indicator integration adds the
read-only imports described below. It records code dependencies, not live runtime health or new
execution authorization. [lifecycle.json](lifecycle.json) is the complete register
of immediate experiment directories, accountable owner roles, intended interfaces,
validation blockers and direct operational imports. Owner roles are maintenance
responsibilities; they do not assert a named person's acceptance or approval.

| Experiment | Status | Accountable owner role | Intended interface |
|---|---|---|---|
| earnings_reaction_v1 | pending validation | Technical research maintainer | Candle/event contracts, source indicator functions and bounded research reports |
| finance_only_v1 | research-only | Financial research maintainer | Decimal financial engine and four-company snapshot pilot |
| historical_candle_archive_v1 | production (bounded integration) | Acquisition operations maintainer | Archive/control APIs through the host lifecycle |
| subsector_index_v1 | research-only | Technical research maintainer | Equal-weight index and constituent math; retained V1 studies |
| subsector_index_v2 | research-only | Technical research maintainer | V2 peer sets and sensitivity reports reusing V1 math |
| technical_fix_v1 | research-only | Technical research maintainer | Retained-data admission repairs and chart/status corrections |
| technical_fix_v2 | production (bounded integration) | Acquisition operations maintainer | Manifest-gated updates and existing technical-data charts |
| technical_integration_v1 | research-only | Technical research maintainer | Retained macro/sector context and completion conventions reused by V2 |
| technical_integration_v2 | pending validation | Technical research maintainer | Index rotation, source validation and narrowed market-context adapters |
| tradingview_scraper_poc | research-only | Technical research maintainer | Offline CSV/decoded packet normalization; vendored code stays isolated |
| unified_financial_pilot | pending validation | Document intelligence maintainer | Reviewed source-linked dossiers through runner/schema/evaluate |
| upstox_fundamentals_pilot | pending validation (blocked) | Source integrations maintainer | Scope/permission documents only; no implemented live adapter |

No directory is declared deprecated: V1 paths still have consumers or retained
reproduction value. Later version numbers alone do not authorize removal.
`research-only` permits isolated research use, not production decisions.
`pending_validation` means the stated acceptance gate remains open.
`production` describes an existing bounded operational interface only; it does
not certify every module, indicator, provider entitlement or trading strategy in
that directory. All existing explicit grants, mount checks and receipt gates
remain required.

## Existing operational dependencies

- `scraper` → `ops/host/scraper_lifecycle.sh` →
  `ops/host/acquisition_lifecycle.py` → `historical_candle_lifecycle.py` →
  archive `control` and `storage`. The installed manifest also pins archive
  sources; see [the historical candle lifecycle](../docs/operations/historical-candle-lifecycle.md).
- `scripts/technical-data` imports `technical_fix_v2.integrate` for charts and
  `technical_fix_v2.update` for updates. Its status/tick path calls
  `ops/host/technical_market_updates.py`, which imports archive calendar,
  storage and control plus the update implementation.
- Those integrations transitively reuse `earnings_reaction_v1` candles, events,
  provider helpers and indicators, `technical_integration_v2` source validation
  and build helpers, and `technical_integration_v1` context helpers. The V2
  chart renderer also imports `technical_fix_v1.repair.SECTOR_BRIDGE`.
  Operational reachability is not validation of their complete research runners.
- `scrapper.research.features.technical.daily_indicators` imports
  `earnings_reaction_v1.indicators` for existing Decimal averaging/RVOL arithmetic
  and `historical_candle_archive_v1.access` for calendar coverage and validated
  indicator segments. This read-only, host-side interface does not open a store
  or authorize acquisition. See the [indicator contract](../docs/research/daily-indicator-engine.md)
  for packaging, calendar, provenance and adjustment limits. These imports do
  not promote the complete earnings experiment to production.
  The financial facade `scrapper.research.features.company_quarter_features`
  remains the financial selection authority; the finance pilot does not replace it.

The register test scans Python source without importing or executing these
modules. It fails on a new experiment directory or an unrecorded direct import
from `scrapper/`, `ops/` or `scripts/`. It does not prove absence of dynamic
imports, shell execution, external installed callers or live processes.

## Promotion and retirement

The accountable role must document inputs/outputs and callers, immutable source
provenance, unit/formula definitions, cutoff and missing-data semantics, offline
regression results and independent review before promotion. Technical research
also requires calendar/adjustment validation, point-in-time universes and a
leakage/execution assessment. Document/model pilots require independent original
source review and frozen evaluation. Source adapters require confirmed retention
rights and explicit bounded access scope. The per-experiment blocker is in the
register. Passing software tests alone does not satisfy these gates.

Promotion requires a reviewed PR updating this index/register and the exact
supported consumer interface. Deployment, migrations, acquisition and runtime
activation require separate explicit authorization. Retire only after tracing
all callers and retained evidence readers, documenting a compatible replacement
and rollback, and preserving source/provenance. No module is moved, deleted,
activated or mathematically substituted by this lifecycle decision.

Research under `scrapper/research/` retains its existing package interfaces and
module documentation; `artifacts/`, `OLD/` and historical source copies retain
their preservation rules. This directory register does not promote those trees.
See [technical indicator definitions](../docs/research/technical-indicators.md)
for shared arithmetic and deliberately separate alternatives.

Offline register check:

```sh
python3 -B -m unittest scrapper.tests.test_experiment_lifecycle -v
```
