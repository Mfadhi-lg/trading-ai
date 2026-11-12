# Daily market updates

Issue #40 uses the existing technical V2 bounded scheduler, provider validation,
raw-response receipts and canonical candle archive. It adds operator commands,
receipt status and scheduler admission locking; it does not install or enable a
timer, extend an owner grant, or alter existing acquisition pipelines.

```sh
scripts/technical-data scheduled-status
scripts/technical-data scheduled-tick
scripts/technical-data status
```

`scheduled-tick` is the entry point for an explicitly approved host timer. It
processes only due checkpoints for the current regular trading session in the
exact `PROPOSED_MANIFEST.json` covered by `OWNER_GRANT.json`. The existing trial
proposal is fixed to 9–13 November 2026; no rolling renewal follows from these
commands. The manifest includes daily refreshes at 09:15 and 16:00 alongside
hourly checkpoints. Existing `update` and finite `tick` commands retain their
behavior. Installation, scheduling frequency, grant renewal and production
activation remain separate operator actions.

A scheduler lock covers both batch manifest publication and execution. Each
checkpoint has a durable batch manifest and an updater ledger bound to its hash.
A repeated completed checkpoint reuses its receipt; overlapping ticks return
`ACQUISITION_BUSY`. A changed or missing batch manifest or mismatched ledger
stops execution. The updater retains original response bytes before validation,
checks their hashes on resume, validates candle bounds/numerics/OHLC and persists
stock observations through the existing archive parser and schema.

Retry a local processing failure by invoking `scheduled-tick` again within the
same approved day. A saved successful HTTP response resumes normalization and
persistence without another provider call. Completed requests remain unchanged.
An uncertain transport delivery, authentication failure or unresolved saved HTTP
error requires owner review; retries never discard receipts or silently repeat
network delivery. Expired dates do not replay intraday snapshots or start new
acquisition. Check the batch ledger and error before deciding on a new grant.

`scheduled-status` reads local manifests and receipts only; it does not contact a
provider, discover a drive, or open the archive. The existing `status` command
also includes this report as `scheduled_updates`. Each due batch shows state,
accepted/quarantined counts, errors and recovery guidance. Stock receipts now
retain `latest_valid_observation_at` from validated source rows; the report shows
the latest such timestamp and its calendar age in seconds. `MISSING` means no
accepted observations in these receipts, and `UNKNOWN` means accepted older
receipts lack the new timestamp. `OBSERVED` reports a timestamp, not complete
universe coverage, trading-session freshness or verified delivery at a historical
decision time. Inspect `incomplete_batches` and per-batch counts as well. Context
feeds and existing archive history are not certified by this stock receipt age.
No historical receipt is backfilled or backdated.

Portable scheduling regressions run in the default offline suite through
`scrapper.tests.test_daily_market_updates`. Filesystem integration tests use
synthetic provider responses, isolated SQLite/Parquet storage and temporary
pause paths; they require PyArrow. No production store or provider is used.
Rollback removes these commands/status fields and scheduler changes; no database
migration is needed. New receipt fields are additive and old receipts remain
readable.
