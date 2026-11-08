# BSE access hold — AR-02.2 offline decision

Treat HTTP 403 from either configured BSE dataset as `ACCESS_BLOCKED`, never as
no publication. The registry's allowlisted official HTTPS APIs and the existing
first-party results-page Referer remain unchanged. A public URL/allowlist is a
technical restriction, not evidence of publisher permission for unattended use.

The scheduler persists `BLOCKED` per dataset in `exchange_source_access_events`.
All later rungs/days skip that dataset without a request, until an explicit
evidence-backed resume. There is no automatic cooldown expiry or retry of a 403.
Unaffected NSE acquisition and its normal guard/retry behavior continue. This is
partial coverage: NSE does not establish BSE-exclusive company coverage.

The executor emits non-ok attempt JSON through the existing structured stderr
alert. Inspect `access_blocked_datasets`, `access_blocked_action`, per-dataset
errors and `--status` dataset states. A dispatch task marked DONE only means its
result was accounted; an `access_blocked` result remains blocked. Verify actual
operator alert delivery during separately authorized production acceptance.

Operator action: retain the hold and recorded 403 history. Review permission and
configuration for the exact official endpoint with the source owner. Only after
separate authorization and documented evidence, resume the named dataset using
the existing `--resume-access` and `--resume-evidence` interface, then run
one bounded official recheck with stop-on-403. Resume alone is
`ACCESS_RECHECK_PENDING`, not success. Never clear both holds by default, reset
task history, replay migrations, introduce cookies/proxies/browser evasion,
disable URL restrictions, or switch to an unapproved source. Permitted
operator-supplied official files may be considered in a separately selected
procedure with provenance and normal parser/guard checks; none is selected here.

Rollback the additive alert guidance by returning to the previous schedule source
in a reviewed candidate. Keep persisted holds, accepted snapshots/versions,
watermarks, pending work, and all retry/access history intact. No data rollback
or production service restart belongs to this offline change.
