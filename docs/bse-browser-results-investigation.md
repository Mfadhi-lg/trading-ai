# BSE browser results investigation — 9 October 2026

Visible Chrome is technically feasible as a small, twice-daily, operator-selected
financial-results fallback. Headless Chrome was denied. The implementation is
read-only and injectable through the existing acquisition fetcher contract;
it is **not activated in the production scheduler**. Production data and both
BSE holds remain untouched.

## Observations and permission

- Ordinary Chrome displayed recent publications on the official financial-results
  page. Visible Playwright with installed Chrome and a fresh context also loaded
  that page: HTTP 200, 4.04 seconds. No imported profile/cookies, stealth flags or
  proxy configuration were used.
- Headless Playwright returned HTTP 403 from that same `.aspx` URL in 1.53 seconds.
  Its earlier Results Announced check also returned 403. These observations do
  not establish a general headless/headed causal explanation or future reliability.
- The full public Results Announced view loaded visibly with HTTP 200 in 1.55
  seconds. Its own integrated-results request returned 26,951 bytes with
  standalone/consolidated content pointers and precise filing timestamps.
- The simpler recent-publications feed lacks reporting scope. It cannot replace
  the integrated feed without inventing filing identities, so the candidate does
  not use it.
- BSE's current footer Website Policies → Terms of Use, clause 13, requires
  express written consent for systematic/automated collection. Clause 8 and the
  disclaimer also restrict copying/reproduction. A twice-daily frequency does not
  itself grant permission. The user confirmed that a hard-copy written consent
  covering automated financial-results collection exists. This is recorded as
  user attestation; the consent document and its precise conditions were not
  independently inspected. Operation must stay within that consent's scope.
- The robots.txt request returned an HTML application shell with HTTP 200,
  not a usable robots policy. It provides no positive permission evidence.

Official pages: [Financial Results](https://www.bseindia.com/corporates/comp_results),
[Results Announced](https://www.bseindia.com/corporates/Comp_ResultsNew).
The current policies were reviewed in the results-page footer modals; older
standalone policy URLs were unavailable to the web tool.

## Comparison with the existing adapter

**Coverage.** The registry HTTP query uses `FlagDur=5&HFQ=3`; the public application's
default observed query used `FlagDur=1&HFQ=`. They are different retrieval windows.
The captured browser response parsed to eight September, seven June and six March
filing identities with zero rejected rows when each period was selected using the
existing parser. Annual/half-year records and equivalent representations were
excluded by its existing rules. This is bounded evidence, not a completeness
audit. Morning and evening page loads are the selected cadence, but historical recovery,
outage catch-up, weekend retention and the exact default-window duration remain
unverified. Do not mark the API's coverage obligation satisfied merely because
this narrower page succeeds. It also does not replace the held security master.

**Source identity.** Both use BSE's own integrated financial-results service.
The browser is an alternative transport/context, not an independent data source.
It observes the application's response rather than issuing or rewriting the
blocked configured API request. The response retains its actual query URL, raw
bytes, HTTP metadata and `auth_mode=public_browser`; it is never labelled as the
configured `FlagDur=5` response.

**Revisions.** Existing `ResultFiling` identity remains security code + period end
+ reporting scope + broadcast instant. Standalone and consolidated pointers are
expanded by the existing parser. A new same-period timestamp remains a revision
candidate for existing manual review; timestamps alone do not establish corrected
financial content. Raw fractional timestamps/content pointers remain available
even where existing normalized identity uses second precision. Late filings are
available only if they appear in the public window. A period-selected read must
not be presented as an all-period revision sweep.

**Reliability.** Visible fresh-context reads succeeded in this session; headless
reads failed. This remains a small sample with no multi-day uptime evidence.
Layout/application changes, Chrome updates, connection failures and access denial
can break it. The candidate observes the known integrated response path and
requires the existing parser's scope-bearing shape. A page shell, empty dataset,
missing scope, rejected rows, HTTP denial or missing results response fails closed.
The current exchange runbook and retained September/October 403 history remain
valid; browser success does not erase that history or resume either hold.

**Costs.** HTTP normally needs one dataset request and no browser processes.
Chrome starts multiple processes and loads many scripts, styles, fonts and
ancillary API requests even though the results payload is only about 27 KB.
Observed page loads took roughly 1.5–4 seconds, but CPU, peak memory and total
transfer bytes were not measured. At two loads/day the operational burden is
plausibly manageable on the user's desktop. It requires a GUI session and
installed Chrome; the current headless production runtime is not validated.

## Implemented fallback

`scrapper/research/bse_browser_results.py` contains:

- One visible Chrome launch, fresh context and one Results Announced page load.
  It passively captures the page's integrated-results response, with no downloads,
  pagination, company crawl, API replay, cookies imported or automatic retry.
- `FetchResponse` and `ParsedResultFilings` contracts, plus an injectable
  `fetch_daily_browser_fallback(url, exchange, ...)` restricted to the configured
  BSE financial-results dataset. It cannot substitute for NSE or the BSE master.
- An exclusive receipt for each morning/evening slot in a selected evidence directory, using IST dates.
  Failed/interrupted attempts consume their slot too. Morning is before noon IST; evening is noon onward. Suggested run times are 09:00 and 18:00 IST. Keep one canonical
  receipt directory; changing directories would create a separate budget.
- No database connection, ingestion, scheduler change or hold-clear operation.
  Normal scheduler runs still skip held datasets. The alternative is explicitly
  operator-selected, rather than an implicit bypass of those holds.

Run the read-only fallback once in the morning and once in the evening on later days:

```sh
python -m scrapper.research.bse_browser_results \
  --consent-evidence 'User attestation: BSE hard-copy written consent for automated financial-results collection' \
  --result-period 2026-09 \
  --receipt-dir artifacts/bse-browser-results
```

Use the project's Python environment with its existing Playwright/requests
dependencies. The command opens visible Chrome, closes it after the bounded
attempt, and saves isolated raw-response/receipt evidence. It does not load
results into production. No background daily job was installed or started.

The final daily command encountered an event-context handling error. That error
was fixed and reproduced in an offline browser-contract test. Its failed receipt
was retained and the daily command was not retried. Consequently the public-page
and payload observations are live evidence, but the corrected daily command has
not yet completed a live end-to-end run. Fourteen offline tests passed, covering
scope, provenance, revision identities, access failures, cleanup, source restriction
and a two-slot IST budget that survives failure and new callers. Legacy daily receipts remain blocking for their original day.

## Decision

Worth keeping as a small local fallback under the attested permission, using
visible Chrome twice daily, morning and evening. It is not yet a complete replacement for the HTTP
adapter or a production-ready automatic failover. The next acceptance check is
one successful daily command on a later day, followed by checking the public
window's retention/overlap against required coverage before enabling ingestion.
No new browser service, queue system or schema is needed for this prototype.

Evidence: `artifacts/bse-browser-investigation-2026-10-09/evidence.json` and its
captured integrated response; existing 403 history remains in
`docs/acquisition-readiness/baselines/AR-02.2/retained-evidence.json`.
