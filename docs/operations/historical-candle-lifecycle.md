# Historical candle backfill lifecycle

The existing `./scraper shutdown` and `./scraper wake` commands now include the
owner-authorized Passport candle campaign and its completion reporter.

Before disconnecting Passport, run:

```sh
./scraper shutdown
```

Wait for `status: PASS`. The command pauses new admissions, lets the current
candle request finish, closes the archive writer and reporter, and performs the
existing shared scraper shutdown checks. A blocked or timed-out shutdown does
not establish that the drive is safe. Once it passes, eject **My Passport** in
Finder and wait for macOS to confirm ejection before unplugging it. The command
does not eject the drive or power off the Mac itself.

After reconnecting Passport, run:

```sh
./scraper wake
```

Wake verifies the external volume identity, the retained authorization and
manifest hashes, and the persistent Python runtime. It resumes the same finite
backfill; successful request receipts are checksum-verified and reused. It does
not authorize new manifests, replay financial schedule slots, or enable recurring
candle-update schedules. Repeating wake while the managed campaign is running
does not launch another copy. A completed campaign is not restarted.

`./scraper status --json` includes `historical_candles`, with the current campaign,
writer and reporter identities and pause state. Runtime logs and lifecycle
verification receipts are under
`artifacts/historical-candle-archive-v1/hcd02/`. Original candle responses and
Parquet files remain exclusively on Passport. Python libraries are retained in
the ignored `.hcd-runtime/deps` directory so restarting does not depend on `/tmp`.

Ordinary shutdown uses cooperative checkpoints and never signals or kills a
writer. The installation handoff is separately recorded: it verified a committed
SQLite boundary and preserved 9,314 successful requests. The subsequent live
cooperative test preserved 9,483 prior requests, allowed one in-flight request to
complete, found zero in-flight requests and no archive writer after draining,
and successfully restarted both managed workers. The existing wake shortcut
then returned `PASS` and `already_awake`.

The dispatcher checksum seal was reconciled only for the authorized lifecycle
change and its candle runtime dependencies. Its previous manifest and the exact
reconciliation are retained in the same evidence directory. Financial and
document schedules, processing logic and authorization remain unchanged.
