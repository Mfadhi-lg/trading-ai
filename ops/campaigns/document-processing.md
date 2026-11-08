# Finite June/browser deterministic extraction campaigns

Offline preparation only. Use an explicitly migrated isolated database with C21.1's 072 contract. No command initializes schema. Freeze uses read-only SQLite; no acquisition, browser access or corpus scan. Each selection has at most 100 artifacts/jobs, and at most 200 original/derivative contents. A new snapshot requires a new reviewed manifest/hash. Do not overwrite a reviewed manifest to admit arrivals.

`python -m scrapper.commands.processing_campaign --db /isolated/scraper.db freeze --campaign-id june-2026-part-1 --period 2026-06-30 --limit 100`

The June selector is the unchanged quarter command's eligible inventory, presentation/transcript media, preference and ordering. Its candidate count includes artifacts without a selectable supported pending content: report the unselected/ineligible remainder; never equate this bounded tranche with a drained June backlog. Repeat inventory on an isolated consistent snapshot before any future retirement decision.

`python -m scrapper.commands.processing_campaign --db /isolated/scraper.db freeze --campaign-id browser-part-1 --run-id browser-ratings-20261001 --browser-job-id 12 --browser-job-id 15`

Browser jobs must already have imported content in that exact run. Capture both original and rendered content, preserve origin/hash/artifact identities, and report all run state counts plus the unselected remainder. This campaign handles deterministic extraction/text normalization only. Existing rating semantic normalization, browser processing evidence, HTML derivative repair and acquisition/follow logic remain owned by the compatible browser workflow. They are explicit residual stages and prohibit retiring its loop based on extraction completion. Imported unsupported media is accounted as unsupported, never usable evidence.

Freeze prints an envelope with `manifest` and `sha256`. Save the inner manifest as JSON and retain its hash separately with operator review evidence. Then:

`python -m scrapper.commands.processing_campaign --db /isolated/scraper.db status --manifest /isolated/june.json --sha256 REVIEWED_HASH`

`python -m scrapper.commands.processing_campaign --db /isolated/scraper.db run --manifest /isolated/june.json --sha256 REVIEWED_HASH --output-dir /isolated/reports --limit 10 --apply`

Run preflights the entire immutable selection/version before writes. It reconciles only those content IDs, consumes at most the requested pending work budget, and exits without following arrivals. Signal drain finishes current work; busy jobs remain pending. Restart with the same manifest/hash recovers exact persisted extraction without re-parsing, including failed/unknown outcomes. Exit 2 means outstanding work remains (including busy/drained jobs); inspect every reported state even on exit 0. `extraction_accounted` means recorded or unsupported, not successful, validated, normalized, or eligible for retirement. Empty June snapshots are truthful no-ops.

Do not activate/modify Compose or remove `june-extraction-worker` or `browser-rating-extract` here. C23.4/C23.5 require independent before/after live work accounting and reviewed role rollout. No current production pending count has been asserted. Future rollout must reconcile the full selected June inventory and all browser residual stages, establish single ownership/drain old roles, apply compatible migrations explicitly, and verify pending/failed/unknown records remain recoverable. Rollback omits this candidate overlay or drains new consumers and resumes legacy processing while retaining all inbox/work/claim/extraction history. Never drop work tables or reset a database as code rollback.
