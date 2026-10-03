# Portable lifecycle state behind `./scraper`

C08.3 is an offline candidate layered on C06 + C08.1 + C08.2. Deployment requires
separate authorization and an exact production identity for the chosen paths.

Export `SCRAPER_SHUTDOWN_STATE_PATH` to select a shutdown/wake state file outside
the source checkout. Unset preserves `<launcher root>/.scraper_shutdown_state`.
Relative paths resolve from the launcher root; absolute paths are recommended.
Parent directories are created only when shutdown records running services.
No state is copied automatically. Keep the same setting for shutdown and wake.
The file remains one container name per line, with `@browser-rating-icra` for the
host browser; old readers can read the file at their selected legacy location.

C08.2 `SCRAPER_BROWSER_BRIDGE_CONTAINER` now also selects the status monitor's
Docker inspect/exec target. Its default remains
`trading-ai-document-download-worker-1`. Browser run ID, PID/log/lock/interpreter
settings continue to pass through the exported environment to host control.
The monitor covers existing rating jobs across runs; it does not filter by run ID.
Settings are exported shell variables, not automatically sourced from `.env`.
The existing document-host-path lookup in `.env` and mount/sentinel check remain.

Shutdown and wake validate configured writable destinations through H03 before
Docker lifecycle actions. Development/test refuse production paths and symlink
aliases; production requires exact authorized identity and writable roots.
Status/settings import/help create no runtime state. Malformed multiline or empty
state settings refuse before actions. The wrapper never evaluates settings as
shell code. Paths with spaces are supported.

Host browser stop completes before bridge/container shutdown. Wake restores
containers before starting the host browser. Failed host stop retains the state
file and prevents container shutdown; failed wake retains state for retry.
Existing paused-container, one-off exclusion and missing-container behavior stay.
No queue policy, retry accounting, DB/schema/content path or stored row changes.

Before changing paths/settings for a running host, stop with the old settings
while its bridge remains alive. Retain the old state and switch settings only
after shutdown. For rollback use the prior launcher/settings and point at the
retained state file; no corpus or database restore is needed.
