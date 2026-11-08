# Trading AI — repository quality and cleanup execution plan

Date: 31 October 2026. Status: proposed execution plan; implementation has not started under this plan.

## Objective and authority

Make the maintained application easy to find, test, reproduce and change while preserving financial history, original documents, research evidence and the working acquisition system.

This plan follows the [North Star](../../TRADING_AI_NORTH_STAR.md), builds on the [26 October audit](../CLEANUP_AUDIT_2026-10-26.md) and [30 October project map](review-2026-10-30/PROJECT_MAP_AND_CLEANUP_PLAN.md), and retains the completed C28.1 structural refactor. It is the proposed sequence for the next repository maintenance cycle. It does not replace product direction, rewrite historical completion records, or certify live runtime health.

The current request authorizes creating this plan. Later execution should select a batch below. Routine reversible work within an authorized batch can proceed without repeated confirmation. Production deployment, service changes, production migrations, credential rotation, history rewriting and removal of retained originals are separate decisions. Prepare exact changes and verification results before requesting any necessary decision.

## Hard constraint — protect the running scraper

Owner instruction, 31 October 2026: a scraper is running and cleanup must not affect it in any way. This constraint governs every batch below and overrides any proposed in-place change that could reach the active system.

- Do not stop, restart, pause, drain, wake, rebuild, recreate, redeploy or reschedule the scraper or its dependencies. Do not invoke operational lifecycle commands during cleanup.
- Preserve active source paths and files, imported host modules, launchers, dispatcher code, configuration, release identities, seals, credentials, locks, receipts, queues, databases, document stores and mounted paths. Running host processes may reload checkout files on later ticks; changing source in this checkout can affect the scraper even without a restart.
- Identify active dependencies using bounded read-only configuration and process metadata inspection before implementation. Avoid broad scans, live database checks, corpus hashing, large archive operations or other I/O/CPU-heavy work that could contend with acquisition.
- Prepare application, host, dependency and test changes in a separate source copy with isolated stores and no production mounts, credentials, scheduler installation or automatic service startup. Verify environment-specific guards before executing tests. Keep resource use modest; run expensive validation away from the acquisition host or defer it.
- Do not move, delete, untrack or replace any path whose active ownership is unresolved. Documentation-only edits may proceed where they are established not to be runtime inputs. Creating this plan does not authorize implementing other batches.
- Do not integrate isolated changes into the active checkout or activate them as part of cleanup. Any later integration requires a separately selected, concrete transition that satisfies the owner's no-impact requirement. If that cannot be established, leave the changes isolated.

Runtime preservation is a completion gate, not merely a rollback provision. A rollback after interruption would not satisfy this instruction.

## Paused research — earnings reaction TA-02A / TA-02B

Owner instruction, 31 October 2026: pause this research until cleanup is complete. This pause applies to the earnings-reaction work, not to the protected running scraper.

Preserve the current checkpoint in place:

- [Five-filing verification manifest](../../artifacts/earnings-reaction-v1/ta02a/event-verification-manifest.csv).
- [TA-02A evidence report and TA-02B retrieval plan](../../artifacts/earnings-reaction-v1/ta02a/README.md).
- Associated `source-evidence.json`, `calendar-evidence.json`, earlier TA-01 records and referenced source snapshots/databases, plus `experiments/earnings_reaction_v1/` implementation and tests.

The evidence report records five filings investigated and zero eligible for historical candle joining. Preserve unresolved publication timing, historical identity, session and filing-relationship checks; cleanup must not promote conditional fields into verified findings.

These are protected ongoing-work inputs and outputs, not archive/removal candidates in H04. Record their dependencies during H01; do not rewrite, relocate, regenerate or run their investigation/retrieval scripts. No TA-02B retrieval, candle joining, provider calls or research expansion is part of cleanup. Any test inclusion changes should be prepared in isolation without changing the checkpoint. After cleanup, report the preserved checkpoint and remaining blockers so the owner can resume from the existing TA-02B plan.

## Baseline and limits

The 31 October sampled inspection found:

- 26,066 paths in the Git index; 23,440 under `artifacts/`, 1,895 under `docs/`, and 643 under `scrapper/`. Index counts include staged additions and are not HEAD-only counts.
- HEAD `0e6ee264`, dated 7 October. Porcelain status contained 823 `A `, 45 ` M`, 2 `AM`, 2 `MM`, 1 `RM`, and 145 `??` entries. Untracked directory entries represent multiple files; these are not a full file census.
- The root README describes an October 23 execution deadline; `docs/STATUS.md` describes October 3 cleanup progress. The October 30 audit records newer scheduling behavior. These statements must be reconciled against current configuration and dated observations.
- CI uses `unittest` discovery. Static counting found 150 module-level `test_` functions in `scrapper/tests` and 81 in `ops/host`; standard unittest discovery does not collect these functions. This is a collection gap, not a count of failing tests.
- Five database migration control tests passed using temporary test storage. The same command without environment configuration produced four errors because writable-target guards require an explicit environment. The host interpreter was Python 3.14; this does not validate the pinned Python 3.12.14 CI environment.
- The October 30 audit identifies active consumers of some `artifacts/` paths, unique changes in the source candidate, and Focus inputs under `OLD/`. Directory names and ignore rules are insufficient evidence for removal.

Refresh relevant facts immediately before a batch. These observations do not constitute a complete correctness, secrets, backup or runtime audit.

## Working rules

1. Keep `scrapper/` as the canonical Python package. Preserve public commands, migration identities and source/financial semantics.
2. Preserve the current index, unstaged work, untracked source, ignored inputs, nested candidate history and recovery information before changing repository membership.
3. Work in small path-scoped batches. Never use blanket staging, reset, clean, stash, recursive artifact removal or history rewriting as a cleanup shortcut.
4. Record dependencies before moving anything: runtime configuration, installed launchers, imports, tests, migration audit scripts, manifests, seals and documentation links.
5. Original financials, document bytes/hashes, attempt histories, labels, availability timestamps and unknown/missing distinctions remain authoritative.
6. Use disposable stores and explicit test configuration for verification. No test should need production volumes, credentials, external document drives, live publishers or paid inference.
7. Historical evidence remains dated. Correct current navigation without editing past reports to imply later knowledge.
8. Disk reduction is measured after verified changes. Logical size, physical reclamation, working-tree reduction and Git-history reduction are separate measurements.

## Target organization

Retain the existing top-level layout and clarify ownership before proposing moves:

```text
scrapper/       canonical application, reusable research implementation, tests
ops/            host integration, dispatcher source, deployment contracts
scripts/        thin developer/audit entrypoints
resources/      reviewed, distributable reference inputs
experiments/    isolated prototypes with explicit status and reproducibility notes
docs/           concise current guidance and dated historical records
.github/        checks that run against a fresh checkout
```

Data, credentials, live receipts, large release bundles and archived evidence need explicit storage contracts. They may remain at existing paths until their consumers and restoration routes support relocation. There is no bulk `artifacts/`, `OLD/`, `ops/activation/` or `docs/cleanup/` move in this plan.

## Batch H01 — preserve and account for the actual workspace

**Purpose:** make ongoing work recoverable before changing its organization.

Work:

- Capture HEAD/branch, staged and unstaged diffs, index modes/blob identities, and expanded untracked and ignored inventories. Store sensitive recovery material outside publication outputs.
- Perform inventories incrementally with modest resource use. Do not copy or hash live stores or large evidence families as a routine preservation step; use existing verified backups and defer any new substantial backup task until it can meet the running-scraper constraint.
- Inventory active runtime path consumers through read-only configuration inspection; distinguish source checkout, installed host entrypoints, pinned images and actual data locations. Do not assume working-tree changes are deployed.
- Establish verified recovery of unique working files and the existing index state. Include nested candidate repository metadata. A patch alone cannot preserve untracked or ignored data.
- Reuse valid database/document backup evidence; assess its freshness, lineage and restoration coverage. If a new database backup is needed, design a consistent SQLite backup including WAL semantics as a separate operational task. Ordinary file copying of a live database is insufficient.
- Classify changes by application, host operations, research, tests, docs, evidence and machine-local state. Identify coupled changes that must stay together.

Deliverables: a compact preservation manifest, recovery instructions and a proposed commit grouping. Keep credential values and private payloads out of reports.

**Done when:** unique work is recoverable, staged versus unstaged versions can be reconstructed, and every subsequent batch has an exact path boundary. Database/corpus recovery gaps are explicit and block batches that depend on them.

**Rollback:** this batch changes no existing project files. Recovery copies remain available throughout the cycle.

## Batch H02 — make automated verification representative

**Depends on:** H01. **Priority:** first engineering change.

Work:

- Establish one documented offline test command using pinned dependencies and Python 3.12.14. Configure `TRADING_AI_ENV=test` and disposable store/document/database paths explicitly.
- Implement and validate this batch in isolated source; do not change dependencies, CI-adjacent configuration consumed by runtime, or application/host files in the active checkout.
- Verify that pytest collects existing unittest classes as well as module-level pytest functions. Include `scrapper/tests`, `ops/host` tests and the existing audit tests; identify additional supported experiment tests by inventory.
- Compare old and new collection sets. Report import/collection errors, skips and missing fixtures rather than silently excluding them. Retain specialized migration checks.
- Remove dependencies on developer-only files from the required CI path: use small redistributable fixtures, deterministic generation, or a clearly separate evidence-dependent suite. Preserve the original evidence and the relevant assertions.
- Add focused linting for syntax/import mistakes and a documented formatting policy. Establish a scoped baseline rather than reformatting the entire repository. Broader typing can follow measured value.
- Run required checks from a fresh source materialization with no production mounts or local secrets. Verify the checks collect the expected host and research tests.

Deliverables: reviewed test configuration, CI updates, developer command and collection summary.

**Done when:** required tests collect and pass in the pinned clean environment; specialized gates still run; exclusions and skips have documented reasons; production/network/paid-inference dependencies are absent from required offline checks.

**Rollback:** revert only this batch's CI/configuration changes; retain the collection comparison as dated evidence.

## Batch H03 — establish one reliable documentation entrypoint

**Depends on:** H01. Can be prepared independently of H02 after preservation.

Work:

- Update root README to explain purpose, maintained directories, offline setup/test commands and the route to current operations.
- Make `docs/STATUS.md` the single current status index. Separate observed state, configured behavior, intended work and unresolved questions, each with a date and supporting reference.
- Reconcile the stale trial/deadline text and missing current-plan link. Mark `docs/current/phases.md` historical or replace its current navigation with a pointer; retain the original record.
- Document host dispatcher versus legacy/manual Compose roles and checkout versus pinned runtime behavior. Keep operational instructions separate from developer setup.
- Retain fixed historical paths needed by tools and evidence. Add navigation rather than renaming all root documents.

**Done when:** current entrypoints agree, changed local links resolve, and a new contributor can find setup, checks, architecture, operational ownership and product direction without reconstructing historical ledgers. Any unobserved live state is explicitly unverified.

**Rollback:** revert only current-document edits; historical records remain available.

## Batch H04 — separate maintained source, runtime inputs and retained evidence

**Depends on:** H01 and H02. H03 supplies current navigation.

Work:

- Refresh the existing path-level candidate list, using classifications: maintained source, required test fixture, active runtime input/state, retained research evidence, archive candidate, regenerable output, unresolved.
- For each proposed move record exact paths, Git membership, owner/consumer, unique-content result, size, destination, sensitivity, restore method and acceptance check.
- First prepare archival of the independent source candidate, root export ZIPs and retired Upstox proposal. Recompare candidate-only/different content and preserve its nested history. Never merge differing source automatically.
- Treat large database snapshots, OCI archives, cleanup patches/harnesses and wheels as separate retention decisions. Identify migration-audit and rollback dependencies before changing their locations.
- Keep small mandatory fixtures accessible to fresh-checkout CI. If an archive remains a required audit input, provide a verified resolvable location before removing its existing path.
- After byte/hash/member verification and a successful restore into a separate location, propose the exact local removal or Git untracking batch. Untracking preserves local files but changes fresh-clone behavior and requires dependency validation.
- Execute archival only for paths proven independent of the running scraper and only where resource contention is avoided. Otherwise prepare the disposition manifest and defer copying/removal; archive work is not allowed to slow acquisition.
- Record that removing blobs from the current index does not shrink existing Git history. History rewriting is a separate project only if needed and explicitly selected.

**Done when:** selected archival bytes and metadata verify, restoration succeeds, active consumers and clean-checkout checks pass, and no unique content is lost. Report actual bytes moved and retained; do not claim all artifact space is reclaimable.

**Rollback:** restore original paths and modes from verified archives and revert only this batch's tracked changes.

## Batch H05 — remove legacy input-path coupling

**Depends on:** H02 and the relevant H04 dependency inventory.

Work:

- Start with the Focus governance builder's `OLD/` dependency identified in the October 30 plan. Add explicit input-root/manifest support to the canonical owner and preserve the compatibility facade.
- Define the required original-input set and provenance contract, including optional OCR evidence and candidate/label inputs. Preserve unknown versus explicitly rejected labels.
- Compare old and new behavior on the same frozen inputs. Account for intentional path changes in provenance without weakening evidence binding.
- Preserve existing defaults until a tested replacement is selected. Archive `OLD/` only after all consumers work from verified retained inputs and restoration is demonstrated.
- Address sample-data and artifact-path coupling in similarly bounded follow-up batches. Keep machine-local live state at existing paths until a separately selected runtime transition.

**Done when:** the selected command works without the historical directory hierarchy using explicit inputs, both entrypoints agree, relevant outputs and semantics match, and original source evidence remains recoverable.

**Rollback:** restore previous input configuration and the batch's source changes; keep original inputs throughout verification.

## Batch H06 — simplify proven maintenance hotspots

**Depends on:** H02; H03/H04 provide the ownership map.

Work:

- Choose one hotspot based on caller/dependency complexity and change frequency. Initial candidates are download-worker legacy hooks, CLI registration/dispatch ownership and host-monitor responsibilities.
- Replace implicit global bindings with explicit, narrow dependencies where existing contracts allow it. Preserve public CLI/import behavior and test seams while migrating internal callers incrementally.
- Document allowed dependency direction. Inspect actual absolute and relative imports before enforcing rules. Add focused boundary checks only where they prevent demonstrated regressions.
- Keep domain rules, persistence, orchestration and host integration responsibilities clear. Avoid splitting files merely to meet a line-count target.
- Remove a compatibility layer only after its callers are inventoried and migrated, and a fresh-checkout check proves it unnecessary. Do not remove a manual worker merely because it is operationally superseded.

**Done when:** the selected hotspot has clearer ownership, affected behavior/contract tests pass, import side effects remain controlled, and the change can be reviewed independently of other cleanup.

**Rollback:** revert the selected hotspot batch. No production activation is implied by source cleanup.

## Batch H07 — close repository and reproduction gaps

**Depends on:** H02–H06 for the scopes actually selected. A documented deferral can close an optional hotspot; it cannot waive a required verification or preservation gate.

Work:

- Build coherent commits from exact reviewed paths. Preserve pre-existing staged/unstaged work rather than automatically committing it all. Couple source changes with their tests and required fixtures.
- Prepare commits in the isolated source environment. Do not switch branches, replace files, or integrate changes in the running scraper's checkout as part of this plan.
- Verify the publication/source manifest excludes credentials, machine state and large local data, and contains all required source, locks, migration identities and redistributable inputs.
- Review named historical credential exposure separately from current-index absence. Any confirmed exposure requires private remediation; do not claim ignores make history safe.
- Reproduce required checks from the final clean source materialization. Compare application behavior, migration identities and relevant fixture/evidence hashes against the preserved baseline.
- Record completed batches, deferred paths, remaining local-only work, archive recovery locations and the actual Git state. Report a clean checkout only if established.

**Done when:** a fresh source checkout passes declared checks, current docs describe it accurately, completed work is reviewable in coherent commits, and remaining local state/evidence has explicit custody.

**Rollback:** use the batch commit and verified retention manifests. Publishing or changing runtime remains separately selected work.

## Execution order and checkpoints

1. H01 preservation and change classification.
2. H02 test collection/environment fixes; H03 documentation reconciliation.
3. H04 one small verified archive batch, then separate large-evidence decisions.
4. H05 one legacy input dependency at a time.
5. H06 one demonstrated code hotspot at a time.
6. H07 final reproduction and reviewable repository state.

Each execution report should contain: exact changed paths, reason, baseline identity, validation command/environment/result, preserved work, actual archive destination if applicable, rollback instructions and unresolved limitations. Keep evidence proportional; routine edits do not need another full repository snapshot or giant archive.

Stop the affected batch if an active consumer is unresolved, archive verification fails, concurrent edits invalidate its baseline, tests could touch production, or a claimed duplicate differs. Investigate or narrow that batch while independent safe work continues.

## Overall acceptance criteria

- One clear maintained source tree, with the existing package and operator contracts preserved.
- One current status index; historical evidence retains its dates and meaning.
- Offline CI collects both supported test styles and relevant host/audit tests in the pinned environment.
- Required checks reproduce without undocumented developer files or production access.
- Every selected move/untracking action has dependency verification and a proven restoration route.
- Current work is preserved and organized into reviewable changes; remaining local work is accounted for.
- Original data, provenance, migration identities and research labels retain their semantics.
- The running scraper's execution, source/configuration, schedule, storage and dependencies remain unaffected. Changes that cannot meet this condition remain isolated or deferred.
- Improvements are reported with evidence, including remaining limitations. Reduced file count alone is not completion.

## Recommended first execution scope

Select **H01**, followed by **H02**. This makes the current work recoverable and establishes checks that can detect regressions in later cleanup. Prepare H03 alongside them. Defer archival removal, large refactoring and production changes until their specific prerequisites are satisfied.
