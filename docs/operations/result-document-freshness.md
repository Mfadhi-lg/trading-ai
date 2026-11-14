# Fresh result and document acquisition

LIVE-FRESH-01 adds an early document handoff to canonical result admission. A
fresh official filing can establish presentation, transcript, recording and
rating expectations before its financial page has been parsed. This requires
an exact current exchange identifier resolving to an eligible application
company. Registry IDs are never treated as application company IDs. Unknown
identities remain in the financial queue for existing resolution and acceptance.

The handoff runs after the financial admission transaction and is recoverable
on the next poll, including partially created expectations. Each invocation
examines at most 100 recent queued/reused filings. It does not admit an extra
financial job or make a network request. Official revisions that retain both
original and revised broadcast columns use their latest official timestamp for
the filing identity, retaining both original source cells.

New expectations for filings observed within 24 hours receive checks at +1,
+15, +30 and +60 minutes, followed by the existing +2h/+6h/+12h and six-week
schedule. Late workers retain spacing between attempts rather than exhausting
missed checks in a loop. Historical filings retain the original retry policy.
Existing schedules, claims, check history and long-tail extensions remain
unchanged on repeated registration. A check becoming due does not override a
source's host permits, cooldown, bounded request capacity or worker schedule.

The bounded discovery worker materializes newly discovered official primary
presentation/transcript links into the existing deduplicated download queue
before attempting the independent Screener refresh. Rating links retain their
existing publisher-specific queue handoff. A second bounded queueing pass
captures links found by that refresh. Download execution remains a separate
stage, with current-quarter priority and immutable content validation.

For a caller-selected isolated database or explicitly authorized read-only
snapshot connection, use:

```sh
python3 -B scripts/document-freshness --snapshot /path/to/consistent-snapshot.sqlite3 \
  --since 2026-11-12T00:00:00+00:00
```

The command requires an explicitly selected quiescent SQLite snapshot, refuses
WAL/SHM/journal sidecars, and opens immutable/read-only with a SELECT authorizer.
It does not locate or connect to a production store. For a caller-owned
read-only SQLite or PostgreSQL application connection, the same report API is:

```python
from scrapper.db.documents.freshness import acquisition_freshness_report
report = acquisition_freshness_report(connection, since_utc="2026-11-12T00:00:00+00:00")
```

The report contains measured official-broadcast-to-first-detection latency,
publication-to-link-discovery latency, canonical download backlog and retained
download request duration/byte throughput. Missing publication timestamps and
future/reversed timestamps are UNKNOWN, never zero delay. Samples are bounded
and truncation is explicit. Request throughput includes failed request time; it
does not claim wall-clock queue throughput or justify parallel network access.
The maintained polling transport records completed response receipt time.
Earlier transport observations may contain polling admission time; the report
labels that retained timestamp limitation and never rewrites old evidence.

The independent poll entry point is prepared in
`ops/activation/result_freshness.py`, with a disabled configuration example.
`plan` and `status` are inert; `tick` without `--execute` admits no work:

```sh
python3 -B ops/activation/result_freshness.py plan \
  --config ops/activation/result_freshness.example.json
```

The reviewed configuration specifies the 5/10/15-minute cadence, monitored IST
window, exact canonical datasets, per-source daily request budgets and evidence
for source-authorized rates. Each due poll requests only the current priority
quarter and its predecessor, allowing current results and late revisions to
retain the existing canonical filing keys, watermarks and queue decisions.
Missed cadence slots are not replayed; the next current feed poll recovers new
filings using their actual official timestamps.

Execution requires `enabled: true`, explicit `--execute`, and a separate
`--owner-grant` JSON with `authorized: true`, the exact configuration-byte
`configuration_sha256`, nonempty `owner_authorization_evidence`, and bounded
`starts_at_utc`/`expires_at_utc` timestamps. Existing dispatcher grants are never
used as polling permission. The existing acquisition owner lock and canonical
exchange executor lock serialize each finite poll. Shared host permits and
retained source access holds apply. Each request reserves an immutable receipt
before network access; quota exhaustion stops admissions, uncertain delivery
blocks further source access, and a failed request leaves a durable source stop
requiring owner review. No timer is installed or configuration activated by
preparing these source files.

`launchd-template --config /reviewed/config.json --owner-grant /reviewed/grant.json`
prints a separate Aqua LaunchAgent property list for review. Prepare its approved
state/log directory before installation. This generator never loads launchd or
changes the existing acquisition LaunchAgent. Installation and an always-on
awake host remain owner actions; launchd cannot poll while the Mac is asleep.

This source change does not increase existing NSE/BSE polling frequency or
document request budgets, renew live grants, or activate acquisition. The
15-minute filing detection target remains unverified until a separately approved
monitored polling window produces real retained timing evidence. Existing Aqua
LaunchAgent configuration is unchanged. No migration is needed.
Rollback consists of reverting the source changes; retain any newly created
events/expectations and original evidence rather than deleting acquisition data.

Offline tests: `scrapper.tests.test_result_document_freshness`,
`scrapper.tests.test_result_polling`,
`scrapper.tests.test_e01_exchange_universe`,
`scrapper.tests.test_d09_artifact_reconciliation`,
`scrapper.tests.test_phase4_discovery_selection` and
`scrapper.tests.test_phase4_routine`, under the engineering guide's isolated test
environment and locked dependencies. All provider responses are synthetic or
committed fixtures; no production database or source access is needed.
