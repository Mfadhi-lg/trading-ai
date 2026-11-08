# Delayed presentation and transcript rechecks

`run_automatic_discovery` selects due company/event groups. `limit` bounds those
groups; each group includes its due, unleased presentation and transcript
expectations. Future checks and active leases remain excluded. The current and
late-publication lanes retain their reserved capacity, with newer expected
publication times first inside each lane. Historical admission stays explicit.

A refresh prefers the company's verified Screener canonical URL over the saved
financial collection path. Only first-party HTTPS company paths are accepted;
consolidated canonical URLs are normalized to standalone paths. Parsed company
identity must still match. Redirects remain disabled and the shared host permit
waits up to ten seconds before the existing administrative deferral path applies.

The worker receipt includes `capacity`: due expectations and company/event counts,
selected groups and expectations, remaining due expectations, and the Screener
request ceiling. Counts describe the selection snapshot, including eligible old
backlog that may require separate historical admission. They do not prove successful
refreshes; use the reconciliation checks and source refresh outcomes for that.

## Capacity configuration

The host dispatcher reads `ops/activation/phase4_schedule.json`. These defaults
preserve the existing daily request allowance:

- `discovery_limit`: 2 company/event groups per run.
- `screener_request_limit`: 2 company-page requests per run.
- `official_request_limit`: 2 official-page requests per run.
- `discovery_hours`: `0,6,12,18` in IST.
- `discovery_daily_slots`: 4, shared by scheduled and manual dispatcher runs.
- `discovery_requests_max_per_day`: 16 total Screener and official requests.

Batch sizes, explicit scheduled hours, daily slots, and daily requests can be
configured separately. The dispatcher conservatively reserves each run's maximum
requests before launching it; interrupted runs retain their reservations. Existing
receipts without request counts cost four discovery requests. Configuration must
reserve another 26 requests for the existing download, patrol, and rating stages
within `source_requests_max_per_day`. Increasing a discovery batch without enough
daily budget therefore reduces the number of admitted runs.

Size capacity from distinct due company/event groups and the target coverage
interval, leaving room for newly reported results and host deferrals. With the
current defaults, at most eight company/event refreshes fit per day, covering up
to sixteen individual document expectations. Hundreds of due companies still
require a larger request allowance or a longer coverage interval. Scheduling
frequency alone cannot resolve that capacity deficit.

Prepare and activate the revised dispatcher/runtime package through the existing
attested release workflow before treating source edits as live. This change does
not modify the active schedule JSON, publish a release, or spend production requests.

## Verification

The delayed-publication regression begins with an event whose transcript is
missing, persists a retry, introduces a later official link, verifies a canonical
queue job, downloads fixture PDF bytes through the real worker, and verifies
persisted content. This is an offline integration check. Live publication coverage
still requires receipts from the deployed scheduled worker.

Discovery, successful download, and accepted document intelligence are separate
milestones. The readiness adapter requires accepted extraction/normalization;
a queued or downloaded document alone does not satisfy that final readiness check.
