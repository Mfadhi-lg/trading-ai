# Common generic executor (C23.2 candidate)

Both generic roles use `run-document-download-worker --eligible-scope NAME`.
Only accepted-screener-concall (presentation/transcript) and da06-rating-recovery
(credit_rating) are eligible. No recording, pilot, browser, direct-rating or
unrelated scope is authorized. Repeat the flag to select both: weighted turns
are three accepted jobs then one recovery job, skipping unavailable lanes.
Finite batches restart with accepted work. Use limits of at least four when
both lanes are selected. Host rotation and durable shared publisher permits
apply across probes and instances; each job retains its own scope policy.

The prepared Compose topology retains the dedicated accepted lane and the
0.6 CPU recovery lane. Do not combine them into one process: a long request
would delay newly accepted results. Commands/flags and bounded lifecycle remain
compatible. Shell-loop replacement is C23.4, not this chunk.

No live rollout is included. A future release must rebuild the exact candidate,
verify registered scopes and additive C19–C22 schemas, drain old owners, then
replace one role at a time. Restore old scope-id commands/image to roll back,
keeping queue rows, leases, attempts and content. Never start both old/new
owners during cutover. Shared permits do not bypass publisher restrictions.

Rollback must retain C20 host enforcement. Legacy scope-id commands do not
automatically opt into shared permits. Before using an unmetered legacy image,
stop every executor that can overlap its reviewed publisher groups, or provide
a separately verified permit-compatible entrypoint. Do not run an unmetered
fallback alongside shared-permit workers. Preserve permit/cooldown records.
