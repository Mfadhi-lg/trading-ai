# Upstox Fundamentals Pilot — Access and Scope

**Checked:** 2026-09-29
**Status:** BLOCKED before live API testing

## Access result

- `UPSTOX_ANALYTICS_TOKEN`: absent from the current execution environment.
- Upstox account eligibility: not verified from this checkout.
- Analytics Token generation permission: not verified.
- Intended automated request volume, response retention, and private research/ML use: unresolved. A free token is not treated as a data-use licence.
- Live API calls: not made. The pilot requires an owner-generated token and confirmed permitted use before any authenticated request.

## Documentation re-check

The official Upstox documentation was re-checked on 2026-09-29:

- Fundamentals launch: https://upstox.com/developer/api-documentation/announcements/company-fundamentals-api/
- Analytics Token: https://upstox.com/developer/api-documentation/analytics-token/
- Income statement: https://upstox.com/developer/api-documentation/get-income-statement/
- Balance sheet: https://upstox.com/developer/api-documentation/get-balance-sheet/
- Cash flow: https://upstox.com/developer/api-documentation/get-cash-flow/
- Rate limits: https://upstox.com/developer/api-documentation/rate-limiting/

The current documentation confirms that:

- Fundamentals endpoints accept ISINs and support standalone/consolidated statements.
- Income statements support yearly and quarterly headline history.
- `full_statement` for income statement, balance sheet, and cash flow is documented as annual, regardless of the requested income-statement frequency.
- Monetary values are documented in INR crore.
- Analytics Tokens are read-only, valid for one year, free, and support Fundamentals without static-IP allowlisting; the documentation does not establish the project's retention, redistribution, automated-volume, or private-ML permissions.
- The published rate-limit page lists generic standard API limits but does not by itself establish a Fundamentals bulk-collection contract.

## Existing repository components

- No existing Upstox adapter or `experiments/upstox_fundamentals_pilot/` implementation was found.
- ISIN-first exchange identity conventions exist in `scrapper/` and the exchange registry artifacts. The pilot must reuse those identities once a real sample is approved.
- `scrapper/core/http.py` is an existing HTTP/provenance utility, but it is currently specialized around Screener and exchange response validation. It is not used for Upstox until a small, isolated adapter is justified.
- No production database, canonical financial-fact ingestion, scheduler, or Screener job was modified.

## Isolated controls established

- Experiment directory: `experiments/upstox_fundamentals_pilot/`
- Raw licensed responses and generated outputs are gitignored.
- No token, bearer header, account credential, or raw provider response was written to disk.

## Required owner action to unblock U2

The owner must generate an Analytics Token in the authorized Upstox account and expose it only through the approved secret/environment mechanism as `UPSTOX_ANALYTICS_TOKEN`. The owner must also confirm whether the intended interactive smoke test, response retention, and later bounded sample use are permitted under the authenticated account terms.

Until those two points are confirmed, the pilot remains stopped at U0. No 2–3 ISIN smoke test, 30-company sample, or bulk request should be run.

