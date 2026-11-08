# Download runtime

The existing Aqua LaunchAgent runs a separate code/configuration copy selected
by `artifacts/acquisition-releases/active.json`. Editing the workspace does not
change the running release. Queue/database volumes, document storage, receipts,
credentials and ownership locks retain their existing locations.

With `concurrent_processing_enabled`, each download batch starts one supervised
processing consumer. Successful content persistence already inserts its inbox
row in the same database transaction. The consumer polls that durable queue,
claims one item at a time, and performs extraction while downloading continues.
It checks again after the downloader finishes, so a final arrival cannot be lost
between an empty queue read and producer shutdown. Existing priorities, content
hash checks, parser-version caching and extraction claims remain authoritative.

Standalone and financial-paired download batches both support this pipeline.
Processing uses the same daily processing/extraction capacity as scheduled
processing, recorded in `processing-budget-reservation.json`. A batch reserves
at most the existing processing limit. Scheduled processing remains available
to drain work when downloads are idle; remaining items stay queued. There is
one downloader and one processor per admitted batch, with short SQLite WAL
transactions on separate connections.

Processor failures are reported against `DOCUMENT_PROCESSING`; successful
downloads retain their own results. Parent failure/timeout terminates the child
process group. The child also exits if its parent disappears. No second cold
database-startup check runs after either worker has started writing.

When `enforce_external_growth_limit` is false, routine document and paired
financial batches check physical drive identity and free space without a full
archive inventory. Receipts mark the inventory as deferred. Enable enforcement
only after explicitly running a full inventory; the old growth ledger is
retained and is no longer updated by routine batches with enforcement disabled.

Full inventories are maintenance operations, invoked while acquisition is idle:

```sh
PYTHONPATH=ops/activation:. python ops/activation/scraper_storage_growth.py --audit
```

Use the configured acquisition Python and site packages for these commands.
To deploy tested workspace changes, prepare a new, uniquely named copy:

```sh
python ops/activation/acquisition_release.py --prepare artifacts/acquisition-releases/RELEASE_NAME
python ops/activation/acquisition_release.py --activate artifacts/acquisition-releases/RELEASE_NAME
```

Activation requires an idle scheduler and owner lock, updates the existing
LaunchAgent, and restores its previous definition and release pointer if loading
fails. It does not start legacy workers, replay consumed slots, or change queue
and source budgets. It requires access to `~/Library/LaunchAgents` and launchctl.
To roll back, activate a retained release with the same command.

Release ownership lock files are hard links to the existing files: symlinks to
host paths are inaccessible inside the Docker `/activation` mount. Release code
remains subject to its runtime manifest. Tests, operator tools and unrelated
experiments do not participate in scheduler runtime attestation.

On a successful scheduler tick, a previous scheduler failure is marked
`RECOVERED`, retaining the original failure record. Per-stage stop files still
require a verified resolution; a scheduler tick does not clear them.
