# Trading AI terminal workflow

The web control panel has been removed. These commands run in the host terminal
using the dispatcher's existing pinned Python environment, Rich, and the existing
production data volume. No Node, browser server, web venv, or additional service
is required for the CLI.

```sh
./scraper                         # Interactive CLI
./scraper shell                   # Same shell explicitly
./scraper-monitor                 # Live terminal monitor
./scraper monitor --refresh 5      # Same monitor; refresh every five seconds
./scraper status                  # Fast readable snapshot
./scraper coverage                # Fresh guarded quarterly coverage + queue diagnosis
./scraper bse                     # Offline explanation of retained BSE browser failure
./scraper monitor --once           # Fast snapshot, then exit
./scraper monitor --once --json    # Machine-readable fast snapshot
./scraper doctor                  # Full guarded inspection with readable output
./scraper status --json            # Full original lifecycle status as JSON
./scraper doctor --json            # Full inspection as JSON
./scraper search CARERATING        # Name, ticker, BSE code, ISIN or database ID
./scraper company CARERATING       # Existing company financial matrices + documents
./scraper company CARERATING --json
./scraper logs
./scraper logs --job JOB_ID --lines 100
```

The shell supports `/monitor`, `/status`, `/coverage`,
`/search`, `/company`, `/bse`, `/doctor`, `/logs`, `/wake`, `/shutdown`, `/clear`, `/help`,
and `/exit`. Financial statement rendering reuses the original CLI's period
matrices. Search retains ticker/identifier matching, including `CARERATING`.
Company search, details and statements are collected under a single guarded read
for `/company`, avoiding three separate Docker round trips.

Unique command prefixes work on Enter: `/compa` or `/comp` opens `/company`,
`/mon` opens `/monitor`, and `/stat` opens `/status`. Company suggestions also
work after `/comp NAME`. Ambiguous prefixes such as `/co` list matching commands
and wait for more input; unknown commands show a short hint instead of argparse
usage.

`/company` and `/search` offer company names and tickers as you type, including
matches on current identifiers and database IDs. Use arrows and Tab to select;
Enter opens the selected company. A bare `/company` or `/search` opens the entry
prompt instead of printing argparse usage. Completion loads a catalog once into
shell memory through the existing guarded query; it does not query production
on each keystroke. During active batches or schedule reservations, suggestions
are deferred, with a toolbar explanation and a one-minute retry cooldown. You
can still enter a known ticker. Without prompt_toolkit, the text commands remain
available with usage hints.

The headline distinguishes **RUNNING** (observed batch process), **WAITING**
(recent scheduler heartbeat and no observed batch), **CHECKING** (missing or stale
heartbeat/process observation), and **PAUSED** (new batches disabled).
BSE and other stage blockers remain visible even while other stages run.
Blockers are retained safety stops, not a count of new failures on each refresh.
When a stop receipt has no error text, the monitor reads the matching batch's
`stop` or `execution_failure` log record to show its cause. This display never
clears a stop or resumes a worker. A source fix in the editable checkout also
does not update the separately pinned active acquisition release.

The candle section refreshes local backfill receipts on each monitor refresh.
It shows source rows by timeframe, companies with nonempty history, companies
with all four planned native histories versus the campaign target, and successful
requests versus planned requests with failures separate. These are lifetime
candle counts, independent of the selected financial reporting quarter. The
campaign state and inventory timestamp remain visible, including while paused.
Counts are batch snapshots, updated by acquisition after each configured batch
and on exit; they are not a per-request archive census or a count of unique,
validated candles. Missing or malformed inventory is shown as unavailable.
The monitor reads only repository JSON receipts and never opens the Passport
archive or issues candle provider requests. `monitor --once --json` includes the
same summary under `feed.historical_candles`.

On patched workers, execution receipts also report saved-data verification:
requests checked in the current manifest, reused requests, activity and its
observation time. Progress is saved at request boundaries at most once per ten
seconds during reuse, so one slow file can delay an update. This is a saved
observation, not proof the worker is still alive. Older workers omit this detail.
The authorized full campaign resumes completed requests from durable SUCCESS
rows in its exclusively owned index, without rereading their files on every
wake. Resumed counts do not certify checksums. Interrupted responses still use
checksum recovery, and uncertain provider delivery still stops without another
GET. The full campaign checks all saved response and Parquet hashes during final
verification before reporting completion. Consequently, damage to a previously
completed file may be detected at that final audit rather than during startup.

`/coverage` refreshes the guarded census and prints a scrollable overview:
stored quarterly result coverage by date and reporting scope, Indian fiscal
quarter labels, collection eligibility, financial work by result period, and
queue diagnostics. Coverage counts distinct companies from accepted parses,
using the quarterly statement's first row of period columns to keep the read
indexed and bounded. Multiple line items or accepted versions do not inflate
counts. This establishes stored period columns, not statement completeness,
financial certification, or a percentage of all listed companies. Nonstandard
quarter ends keep their source dates rather than getting invented FY labels.

Pending means waiting queue work. Retryable means a stored past failure, not an
active retry. Diagnostics separate never-attempted work, retries whose stored
time is due, and future retry times, and show the largest retained failure
reasons. Due time does not establish current eligibility: scope, policy,
attempt limits, authorization, and bounded schedule still apply. Historical
rows remain in totals. Small screens show the quarterly summary and link to
`/coverage` for full detail; counts retain their census timestamp.

`/bse` replays the latest retained raw browser response locally and shows HTTP
status, valid filing identities, rejected rows and their parser reasons. It
does not launch Chrome or retry acquisition. On 24 October the parser gained
explicit exclusion handling for non-quarterly `SN`/`DN` records with consecutive
fiscal years. These records remain in the raw response and never become invented
quarterly filings. The retained 22 October evening response then passed with 49
valid filing identities, and guarded ingestion queued 12 company jobs. The BSE
stop was archived after successful ingestion. Malformed period codes continue
to reject the batch.

Financial claim loss ends the current slot with exit 75 and a deferred outcome.
The worker fences persistence and releases only its own unfinished claims. The
consumed slot is never replayed; a later scheduled slot can acquire fresh claims.
Authentication, source validation and other failures still retain a stop.

NSE request timeouts also defer the consumed slot (exit 75). They retain the
period and error in a `source_transport_deferred` receipt and permit a fresh
attempt only at the next eligible scheduled slot. They do not retry within the
failed slot or permanently disable NSE acquisition. Source rejections and
schema/identity failures still stop for review. The scheduled BSE container
mounts and hash-checks migration 076, already applied to production, so the
retained application image can verify the current migration ledger without
running migrations.

## What the live screen means

`/monitor` opens the combined dashboard.
The panel layout puts financial scraping, document downloads, credit ratings,
and processing together. It shows queue counts for the selected reporting
quarter, standalone/consolidated financial coverage, recent financial outcomes,
document download and extraction states, observed workers, blockers, and the
next schedule slot. Rating document statuses come from the rating queue when
present. Recent financial receipts remain visible between database observations.
Use `--quarter YYYY-MM`, `--all-quarters`, or `--quarter unknown` to change scope.
In the live view, `r` refreshes, `c` selects the latest quarter, `[` and `]` step
through quarters, `q` opens the picker, `a` selects all, and `u` selects unassigned.
Short terminals retain all pipeline counts, the latest financial outcome,
blockers, and controls; larger terminals show separate financial and document
panels. `/coverage` retains the full scrollable quarter details. The legacy
percentage and ETA are omitted because historical queue totals cannot establish
remaining eligible work or a completion time.

The familiar terminal panels show financial/document/rating queues, pending and
claimed/leased rows, accepted/saved outcomes, skips and failures; company results
with reporting periods/scopes; active bounded adapters; recent batch outcomes and
durations; the next schedule opportunity; drive capacity; document authorization;
and retained blockers. `./scraper status` also prints the fuller history/schedule.
The monitor uses an alternate screen; Ctrl+C returns to the shell. Short terminals automatically use a compact layout that keeps the drive, pause
state and blockers visible. `./scraper status` prints the fuller scrollable snapshot.

Receipt refresh is local and immediate, defaults to five seconds, and never
opens Docker or SQLite. Recent financial outcomes are joined to their claimed
queue items in the same bounded batch receipt, so symbols and reporting periods
remain visible while a database census is deferred. A growing request log can
show the last requested company, but an unfinished receipt alone is not called a
live worker. Process checks run independently every ten seconds.

A background guarded inspection runs every 120 seconds (`--inspect-every` accepts
60 seconds or longer). It validates the existing workflow, drive and production
volume and only reads the database in the existing safe idle window. It never
starts a scraping worker, resumes acquisition, replays missed slots, or opens a
host fallback database. It may temporarily start the existing networkless,
read-only census container. Browser/source access is never initiated by the CLI.

Every queue total retains its census timestamp. The initial screen can use the
last successful shutdown/wake verification, explicitly marked as a saved
observation. Later failed or deferred inspections retain the last good counts
with their original timestamp and display the current reason. Counts include
historical work and do not promise eligibility or an all-backlog completion ETA.
Historical `LEASED` rows do not by themselves prove a live lease or worker.

Company queries use the existing scheduler/owner locks, cold SQLite checks and
lease checks. Active work or the two-minute reservation before a scheduled slot
can defer them; retry after the batch. The CLI never bypasses these checks.

Ctrl+C stops monitoring, not acquisition. If a guarded read is already in
progress, the monitor finishes that read before releasing its ownership locks.
The terminal exit can therefore briefly wait for an existing read-only check.

## Lifecycle

```sh
./scraper shutdown
# Wait for PASS, then eject My Passport in Finder before unplugging.
./scraper wake
```

These remain the current guarded shutdown/wake commands. The CLI shell's typed
`/wake` and `/shutdown` invoke those same commands. Continuous legacy workers,
queue resets and requeue shortcuts are not restored as automatic workflow steps.
Repeating `wake` while the dispatcher is loaded and admissions are enabled
returns `already_awake` without draining active scheduled work, provided candle
workers are already running or are not configured. Resuming paused acquisition
still requires the ownership locks and all existing safety checks.
`./scraper dashboard` explains that the web panel was removed.

Source and financial/document backup guidance is preserved in
[security and backup notes](security-backup.md).

## Validation

Use the prepared LaunchAgent's Python and PYTHONPATH to run:

```sh
python -B -m pytest -q ops/host/test_acquisition_terminal.py \
  ops/host/test_acquisition_observations.py ops/host/test_acquisition_lifecycle.py \
  ops/activation/test_automatic_acquisition.py
```

Tests use isolated receipts and databases. They cover fast startup without Docker,
receipt completion, company-to-result matching, saved-count timestamps, deferred
queries, literal diagnostics, pause state, CLI routing, company matrices, and the
existing dispatcher/lifecycle protections.
