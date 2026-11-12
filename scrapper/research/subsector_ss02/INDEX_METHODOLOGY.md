# SS-02 basket methodology and calculation boundary

Reviewed for issue #22 on 12 November 2026 against `policy.py`,
`full_universe.py`, `build.monthly_metadata`, `003_subsector_baskets.sql`,
and the SS-03/04 overlays. This documents research basket metadata, not an
activated index or a verified historical trading universe.

## Constituents and historical membership

The candidate universe is the retained canonical-company collection. SS-02
combines current business exposures with dated NSE basic-industry records for
candidate discovery. Current exposures alone never establish historical
eligibility. Exact retained security links resolve rows to canonical company IDs;
ambiguous links remain unresolved. Companies are ranked once per subsector and
quarter, not once per security, and duplicate canonical IDs are rejected by the
selection function. The existing historical ingestion dictionary keeps one record
per company per quarter; conflicting exchange rows require separate evidence
review rather than treating multiple securities as independent companies.

Each candidate must have contemporaneous metadata whose fine basic industry maps
to that subsector in `taxonomy.HISTORICAL_INDUSTRY`. It must also have:

- A timezone-aware publisher availability timestamp at or before the cutoff.
  Unknown, malformed and timezone-naive availability cannot qualify.
- An observation date on or before the cutoff's local calendar date.
- Finite, strictly positive retained `Index Market Capitalisation (Rs. Crores)`.
- Finite average impact cost from 0 through 0.5 **percent**, inclusive.
- A retained historical quote-presence record and a non-placeholder symbol.

The versioned cutoffs are FY2026-Q4: 31 March 2026 23:59:59 IST, and FY2027-Q1:
30 June 2026 23:59:59 IST. Q4 reads February metadata; Q1 reads April metadata.
Publisher timestamps are compared as instants across timezone offsets. A later
report or later business classification cannot populate an earlier basket.
Unknown evidence means `requires_historical_evidence`, not proven ineligibility.

`ss02_baskets` identifies each reconstruction by code, quarter, cutoff, policy
hash and retrospective freeze time. `ss02_basket_members` retains selection
metadata and provenance. These are discrete cutoff snapshots: there is no daily
effective-membership history or assumed carry-forward between quarters. The
SS-03 source memberships remain review candidates, and undated SS-04 decisions
remain retrospective classifications. Neither supplies missing historical proof.

A source report's publisher modification timestamp is an attestation, not an
independent archive of when a researcher could first obtain the data. Starting
from the existing collection also leaves survivorship and missing-company bias
unresolved. These baskets must not be described as point-in-time certified.

## Ranking and initial weights

Select at most `TOP_N=3` eligible canonical companies by descending historical
index capitalization, breaking ties by ascending canonical company ID. Returns,
beta, current capitalization and future performance are not ranking inputs.
Fewer than three eligible companies produce a smaller basket; none produces an
empty candidate-only basket. No unqualified company fills a vacancy.

For the selected set S with N members and capitalization C_i in crore:

- `equal_weight_i = 1 / N`
- `historical_cap_weight_i = C_i / sum(C_j for j in S)`

Only selected members enter the denominator. For capitals 300, 200 and 100 crore,
initial cap weights are 1/2, 1/3 and 1/6; equal weights are each 1/3. A fourth
company ranked below the first three does not change those weights.

`initial_members` uses decimal arithmetic with 50 significant digits, then emits
floats for the existing JSON/SQLite REAL contract. Repeating fractions are
rounded at that boundary; normalization tests use a tolerance. The upstream
historical parser already stores binary floats, so converting retained numbers
to decimal does not recover original source precision or change source bytes.
The field is the publisher's **index** capitalization, not an assertion of full
company market cap or a newly calculated investible-weight factor.

[Nifty Indices' calculation tutorial](https://niftyindices.com/resources/tutorial/calculation-of-indices)
describes capitalization-based weighting and distinguishes full from free-float
capitalization. It supports the weighting principle; it does not validate this
custom basket as an official NSE index.

## Index levels, benchmark comparisons and execution

The implementation does not calculate index levels, constituent returns or
benchmark excess returns. Initial weights are not daily rebalanced weights or a
buy-and-hold return series. All manifests retain `chart_ready=0`. The historical
quote evidence proves only a quote was observed, not complete price coverage,
listing continuity or executable liquidity throughout a holding period.

No performance comparison to Nifty 50, Nifty 500 or a sector benchmark is
currently supported. A future comparison requires matching observation dates,
currency, price-return versus total-return treatment, corporate actions,
suspensions/delistings, rebalance and effective-membership rules, and benchmark
history. It must state whether excess return means arithmetic return difference
or relative wealth. A trading backtest additionally needs next-available
execution timing, costs, slippage and an unbiased historical universe. Do not
manufacture a comparison from present-day constituents or month-end quotes.

## Reproduction and preservation

Run portable synthetic checks without opening retained databases or the network:

```sh
python3 -B -m unittest scrapper.tests.test_subsector_index_methodology scrapper.tests.test_subsector_ss02 scrapper.tests.test_subsector_portable_contracts -v
```

These cover cutoff and timezone boundaries, changing historical classifications,
missing and invalid evidence, deterministic top-three selection, duplicate
rejection, hand-calculated weights, sparse baskets, and overflow resistance.
The overlay tests separately check source preservation and frozen-plan fencing.
Tests do not certify retained source files or historical universe completeness.

No migration, production activation, retained-plan regeneration or data rewrite
is required by this validation change. Existing frozen inputs remain evidence;
code/plan mismatches must continue to refuse silent replacement. Any new
reconstruction needs an explicitly versioned research run under the existing
preservation rules.
