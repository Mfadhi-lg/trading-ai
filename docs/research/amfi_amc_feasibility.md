# AMFI / AMC Monthly Portfolio Replacement-Source Feasibility

_Assessment date: 2026-08-04_

## Decision

The replacement is **technically feasible but not yet approved for acquisition**.
Official monthly portfolio spreadsheets can supply the core position fields and
support deterministic month-over-month derivations. Rights, source-specific
terms, current URL discovery and exact request counts remain hard gates before
any portfolio file is downloaded.

P03 remains paused. No further Trendlyne request is authorized, and the existing
Trendlyne fixtures must not be expanded, republished or used as a seed for the
replacement dataset.

## Official basis

The current SEBI Mutual Funds Master Circular (20 March 2026), paragraph 6.1.1,
requires mutual funds/AMCs to disclose month-end portfolios with ISINs on the AMC
and AMFI websites within 10 calendar days, in a downloadable spreadsheet. Format
4C includes instrument name, quantity, market value in INR lakhs and percentage
of NAV. It also requires the entire portfolio to be disclosed, regardless of a
position's size.

This makes SEBI Format 4C the semantic contract, not a particular AMC workbook
layout. It does not itself grant data-reuse rights.

Official references:

- [SEBI 2026 Mutual Funds Master Circular](https://www.sebi.gov.in/legal/master-circulars/mar-2026/master-circular-for-mutual-funds_100491.html)
- [AMFI Portfolio Disclosure directory](https://www.amfiindia.com/online-center/portfolio-disclosure)
- [AMFI Terms of Use](https://www.amfiindia.com/terms-of-use)
- [AMFI robots guidance](https://www.amfiindia.com/robots.txt)

The AMFI directory currently exposes 57 AMC records, of which 51 have a non-empty
monthly-portfolio URL and 6 do not. The directory is useful as an official routing
registry, but it is not sufficient evidence that each destination URL is current:
one representative ICICI Prudential URL returned 404, while HDFC returned 403 to
the non-browser feasibility request.

AMFI's Terms of Use limit site access/use to personal, non-commercial purposes and
restrict unapproved modification or reverse engineering. Therefore AMFI page or
file ingestion for model use is blocked unless the intended use is confirmed to
fit those terms or written permission is obtained. Each AMC's terms, copyright
notice and file-level notices require an independent review; the fact that a
spreadsheet is regulator-mandated and publicly downloadable is not treated as a
reuse licence.

## Requested-field feasibility

| Required field | Source or derivation | Feasibility | Important constraint |
|---|---|---:|---|
| Fund/AMC identity | AMFI directory plus workbook/header | High | AMFI `mf_id` is a source identifier, not the internal primary key. |
| Scheme identity | Workbook sheet/header plus official AMC/AMFI scheme identifiers | Medium-high | Plans/options and schemes must not be conflated; renames and mergers need effective-dated aliases. |
| Security name | Format 4C instrument name | High | Preserve source text; do not use it as the primary join key when ISIN exists. |
| Security ISIN | Mandated portfolio ISIN | High | Cash, receivables and some derivatives may legitimately have no ISIN. |
| Shares/quantity | Format 4C quantity | High | `shares_held` is valid only for equity-share units; other instruments retain a generic quantity and unit. |
| Market value | Format 4C market value | High | Normally displayed in INR lakhs; retain display/unit and normalize with scale `100000`. Never map this to Trendlyne `aum`. |
| Portfolio percentage | Format 4C `% to NAV` | High | Store as percentage points and preserve `<0.01`/asterisk conventions. |
| Month-over-month change | Difference of two accepted positions | High | Requires the same effective scheme identity, security identity and quantity unit in consecutive periods. |
| Buyer/seller/holder | Derived from accepted consecutive positions | High for equity | Missing disclosure is not zero. Entry, exit, unchanged and corporate-action ambiguity remain distinct states. |
| Index/arbitrage classification | Versioned official scheme category/SID or AMC classification | Medium | Never infer from the scheme name. Holdings workbooks alone may not contain category. |
| Publication date | Explicit AMC page/file date when present | Medium | The regulatory due date is not the actual publication date. HTTP `Last-Modified` is evidence, not automatically issuer publication time. |
| Availability date | Explicit publication timestamp, otherwise first observation | High | Keep period end, publication, first observation and download time separate. |

## Derived activity definitions

Derive activity only from two accepted portfolio versions for the same scheme and
security:

```text
quantity_delta = current_quantity - prior_quantity

entered  = prior absent/zero and current > 0
exited   = prior > 0 and current absent/zero
buyer    = quantity_delta > 0
seller   = quantity_delta < 0
holder   = current equity quantity > 0
unchanged = quantity_delta = 0 and current equity quantity > 0
```

An absent scheme file, failed parse or unresolved scheme identity produces
`unknown`, not a zero position. Corporate actions, ISIN replacement, mergers,
splits and quantity-unit changes must suppress the unadjusted buyer/seller label
and emit an anomaly until reconciled.

For a company/security month, buyer, seller and holder counts are counts of
eligible schemes after classification filters. Net change is the `Decimal` sum of
eligible equity-share quantity deltas. The exact scheme set and filter version are
part of the computation basis.

## Important regulatory and source exceptions

- The general disclosure rule is monthly, but the 2026 Master Circular contains
  specific passive-scheme provisions, including quarterly disclosure language for
  equity passive schemes. The scheduler must use an effective-dated cadence rule
  and must not fabricate a missing month.
- Debt schemes also have fortnightly requirements. Those files are outside the
  initial equity-company reconstruction scope and must not be accidentally mixed
  into monthly position changes.
- AMC indexes may be server-rendered, client-rendered, API-backed, ZIP archives,
  consolidated workbooks, or one file per scheme. Discovery and parsing are
  separate source contracts.
- The AMFI directory may lag an AMC redesign. A destination identity/status check
  is required before accepting its links.
- Same-URL revisions are possible. Every downloaded response must be immutable and
  content-addressed; a corrected workbook creates a new accepted version.

## Rights and acquisition gates

No holdings-file request may occur until all of these are recorded:

1. source owner and publisher;
2. terms/copyright URL and reviewed version/date;
3. allowed purpose: local research, model training, derived metrics, retention and
   redistribution considered separately;
4. robots/request guidance and authentication boundary;
5. exact discovery and file URLs for the proposed batch;
6. exact page/API/file request counts by host;
7. rate limit, retry limit and maximum file size;
8. explicit approval decision: `approved`, `personal_only`, `permission_required`,
   `prohibited`, or `unknown`.

`personal_only`, `permission_required`, `prohibited` and `unknown` block bulk
acquisition. Derived output must never redistribute source workbooks or reproduce
substantial source tables unless the rights decision explicitly permits it.

## Request-count model

Current discovery inventory (not a download approval):

```text
AMFI directory records                         57
non-empty AMC monthly disclosure destinations 51
blank AMC monthly disclosure destinations      6
```

The initial rights/discovery audit has no portfolio-file downloads. For each AMC
it may require one terms page, one robots file, one disclosure index and zero or
more API calls used by that index. These metadata requests must themselves be
printed before execution and run in small host-bounded batches.

After rights approval, file counts are:

```text
consolidated-file case = approved_AMCs × months
scheme-file case       = sum(approved schemes per AMC-month)
total requests         = index pages + index APIs + portfolio files
```

At the present inventory ceiling, 51 approved AMCs over 12 months would be at
least 612 portfolio-file requests in the consolidated-file case, before index/API
requests. The per-scheme case is intentionally not estimated: discovery must first
produce the exact scheme/file list. No batch should be approved from an inferred
count.

## Recommended feasibility pilot after rights approval

Use three structurally different, explicitly approved AMCs and two adjacent
months. Prefer one consolidated workbook source, one dynamic index/API source and
one multi-file source. Before fetching, print every index/API/file URL, expected
content type and host-grouped request count. The pilot exits only when:

- every workbook/sheet is inventoried by a source manifest;
- all scheme and security identities are either mapped or explicitly unknown;
- source rows and display cells are preserved before normalization;
- month-over-month classifications reproduce from accepted positions;
- publication/availability metadata is complete;
- no unknown workbook component is silently skipped;
- source terms permit the intended retained and derived use.

## Conclusion

The official disclosures can replace the numerical holdings core and improve
security identity through ISIN. They cannot be treated as a drop-in Trendlyne
parser replacement: scheme identity, category classification, file discovery,
cadence and usage rights are separate versioned concerns. Proceed only with the
metadata/rights audit; keep all portfolio-file download commands disabled until a
reviewed request plan is explicitly approved.
