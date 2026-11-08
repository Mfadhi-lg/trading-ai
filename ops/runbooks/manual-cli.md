# Manual CLI profile (offline candidate)

Use `docker compose --profile manual run --rm cli <command> <flags>` or the compatible `./scraper <command> <flags>`. No arguments to `./scraper` still opens the interactive shell. The cli service defaults to `--help` and has no restart policy, scheduler, dependencies or queue-draining default. CLI commands, aliases, JSON output and exit codes still belong to `scrapper.cli`.

The five old service names (`discovery`, `worker`, `exchange-universe`, `exchange-results`, `exchange-reconciliation`) retain their exact commands, settings and mounts under `manual-compat`. Explicit `docker compose run --rm worker` (or `--profile manual run --rm worker`) still selects that service automatically. `manual` selects only the new CLI among one-off roles; `manual-compat` selects the old definitions. Continuous services remain configured and are unchanged. Use `run`, not a broad `up`, for one-off commands.

Examples:

```sh
docker compose --profile manual run --rm cli screener-discover-results --query-role current_reporting_cycle --query 'Last result date > 202605'
docker compose --profile manual run --rm cli exchange-refresh-universe --dataset nse_equity_master --file /inbox/master.csv
docker compose --profile manual run --rm cli exchange-discover-results --dataset nse_financial_results --result-period 2026-06
docker compose --profile manual run --rm cli exchange-reconciliation-report
docker compose --profile manual run --rm cli screener-company-worker --due --limit 5
```

The generic CLI combines discovery's core identity/store/session with the exchange read-only inbox. It adds no document-store mount or external document write authorization. Worker-specific environment (`SCREENER_THROTTLE_SECONDS=4`, `SCREENER_THROTTLE_JITTER=1.5`, `SCREENER_WORKER_LIMIT=5`) remains on the compatible `worker` service; use that service when those exact settings are required. Generic CLI flags/defaults are those formerly available through `./scraper` on discovery. Manual runs must not overlap an active owner for the same work; this profile is not a new claim or scheduling policy.

For development, combine `compose.yml` and `compose.development.yml`; cli uses isolated development volumes and no production identity configs. Production image/identity remains the retained C05.3 reference for configuration parity, not a rebuilt image containing all later candidate overlays. A future separately authorized release must build/verify the frozen candidate. This offline chunk starts no Compose service, mounts no live data, applies no production migration and performs no main integration.

Rollback before release: omit this overlay. After a later authorized rollout: finish/stop one-off CLI runs, select the previous configuration and launcher, keep queue/history/schema unchanged, and resume only one owner. No database restore or queue reset is needed. Keep the C05.3 release manifest immutable; C23.1's separate topology manifest locks the two changed Compose files for the release audit.
