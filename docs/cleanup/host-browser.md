# Host browser configuration — C08.2

Implemented only in the isolated C08.2 candidate. The incremental patch applies
to the retained C08.1 source; neither candidate is activated. Existing recovery
policy, host marker `@browser-rating-icra`, CLI actions and shutdown state stay
compatible. C08.3 owns lifecycle state portability and remaining wrapper references.

| Setting | Default / owner |
|---|---|
| `SCRAPER_BROWSER_STATE_PATH` | When explicitly set, C08.1 browser-state root owns PID/lock; otherwise existing `artifacts/icra-download-test` |
| `SCRAPER_LOGS_PATH` | When explicitly set, C08.1 log root owns host log; otherwise existing campaign folder |
| `SCRAPER_BROWSER_PID_PATH` | State root / `icra-recovery.pid`; exact file override |
| `SCRAPER_BROWSER_LOCK_PATH` | State root / `icra-lifecycle.lock`; exact file override |
| `SCRAPER_BROWSER_LOG_PATH` | Log root / `icra-recovery.log`; append-only exact file override |
| `SCRAPER_BROWSER_PYTHON` | Existing campaign `.venv/bin/python`; explicit interpreter override uses locked preflight |
| `SCRAPER_BROWSER_RUN_ID` | Existing `browser-ratings-20261001`; same selection for claim/import/release |
| `SCRAPER_BROWSER_BRIDGE_CONTAINER` | Existing `trading-ai-document-download-worker-1`; same bridge for worker/stop |
| `PLAYWRIGHT_BROWSERS_PATH` | Existing Playwright cache when unset; explicit absolute cache root is H03 checked |

Use absolute paths and one stable PID/lock pair for a host worker. Changing
settings does not discover or relocate old state. Stop through the old settings
while the bridge is alive before selecting new settings; retain old PID/log/lock
files for rollback. Never switch paths or run/container selection under a running
worker: a different PID/lock pair could create a second owner. This chunk does
not change queue fencing or add multi-host ownership guarantees.

Imports/help/status/is-running create no runtime files. Start/stop validate all
configured PID/log/lock and explicit browser-cache destinations through H03
before creating directories, opening a lock/log, signaling or invoking the bridge.
Production external destinations need exact identity authorization. Do not copy
the container identity manifest to the host: resolved host DB/store/document
identities and authorized extra write roots must match. Missing identity fails
closed. Existing document capacity/sentinel/staleness checks remain intact.

The shared standard-library settings module owns the bridge command. The worker
still closes its browser and bridge before host stop releases the interrupted
claim. `./scraper shutdown` still closes the host before stopping containers;
wake still starts the bridge before the host. The old shutdown-state reader and
host marker remain unchanged. Wrapper monitor container references remain for
C08.3; the environment applies only to the host recovery bridge here.

## Rebuild the environment outside campaign output

The host lock preserves the observed host versions: Python 3.14, Playwright
1.60.0, greenlet 3.5.6, pyee 13.0.1 and typing-extensions 4.16.0. It is distinct
from the C06 Linux container lock. Playwright selects Chromium revision 1223,
Chrome for Testing 148.0.7778.96. The lock includes package distribution hashes.
Reproduction was verified on macOS arm64, Python 3.14.5. The initial rebuild used
the local uv cache; the subsequent hash-required reinstall downloaded the pinned
Playwright wheel and verified the distribution lock.

Example for an isolated candidate, after choosing authorized locations:

```sh
python3.14 -m venv /absolute/private/browser-env
/absolute/private/browser-env/bin/python -m pip install --require-hashes -r ops/host/browser-requirements.lock
PLAYWRIGHT_BROWSERS_PATH=/absolute/private/browser-cache /absolute/private/browser-env/bin/python -m playwright install chromium
SCRAPER_BROWSER_PYTHON=/absolute/private/browser-env/bin/python PLAYWRIGHT_BROWSERS_PATH=/absolute/private/browser-cache python3 -B -m scrapper.commands.browser_rating_host check-environment
```

Provisioning is an explicit operator step; start never installs dependencies or
downloads a browser. `check-environment` verifies Python, exact package versions
and the Playwright-selected Chromium executable without opening a browser or
DB bridge. It starts the Playwright driver for this explicit check. An explicit
interpreter override must pass that preflight before worker launch; the legacy
unset interpreter remains usable without a new version requirement. Preflight
failure blocks startup. Operator environment settings are inherited by the
worker; no secret file is created or sourced. Worker imports use the selected
candidate source root through `PYTHONPATH`, so an external venv works.
Direct file invocation, used by the existing wrapper, also locates the package
from its own source root without requiring an operator `PYTHONPATH` setting.

Offline reproduction used a newly built external venv and the existing browser
cache for the executable check. Browser binary download, visible browser launch,
publisher traffic and production claim/release were not exercised. Chromium
version selection is pinned by Playwright; its binary is not separately archived
or independently checksum-certified by this chunk.

Rollback restores the two original candidate commands and removes the added
settings/test/lock/contract. Select prior environment settings after the worker
has stopped; preserve runtime files. No data restore, content relocation or
stored-path rewrite is involved. Next selected step: C08.3 only.
