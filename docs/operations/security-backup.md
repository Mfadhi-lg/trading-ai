# Credential remediation and recoverable backups

Audit performed 22 October 2026. No push, commit, branch removal, history rewrite,
credential renewal, or acquisition transition was performed.

## Exposure findings

`.screener_csrf` contains a credential in reachable local commit
`e71d1ee168d4a2b5c155aa6302347aeaad841e46`. Deleting it at HEAD did not remove it
from ancestry. The value was not printed or included in this report. Treat the
historical credential and associated session as exposed.

Read-only GitHub API filename-history queries returned no commits for that path
on all three advertised remote branches. `git ls-remote --heads --tags origin`
reported these branch tips and no tags:

- `main`: `0ae886aee18e72fb67ede9c530ae85da74ce12dc`
- `cleanup/c08.3-lifecycle-portability`: `67ab494bc83e6324018ee5560638dcf67ad7e4d9`
- `codex/rating-recovery-lifecycle`: `8527248644742554cfd024809d749208b5b9d9de`

GitHub's comparison of main with the exposed local SHA returned 404. Evidence
supports **confirmed local exposure; no filename-history exposure found in the
currently advertised remote branches**. This is not proof of absence from
renamed files, deleted refs, pull request refs, forks, caches, or other secrets
inside binary archives. No full remote-object content audit is asserted. Do not
push the local ancestry or assume existing remote branches are fully sanitized.

## Credential maintenance: blocked until a safe window

The running acquisition uses `.screener_session` and may depend on the associated
CSRF/session pair. Invalidating the session now could interrupt it. Therefore
rotation was deliberately deferred under the user's no-interruption instruction.
The Control Center reports backup health as blocked.

During an explicitly approved maintenance window:

1. Use the guarded CLI shutdown; require its quiescence result and retain pause.
2. In the authenticated Screener account, revoke/logout the affected sessions
   through supported account controls. If there is no revocation control,
   coordinate with the provider. Do not assume deleting a local CSRF file revokes
   server credentials or that a historical CSRF token is harmless.
3. Authenticate again and replace the restricted local session/CSRF files using
   the established credential workflow. Never log tokens or put them in Git,
   shell history, screenshots, or source backup.
4. Verify the old session is rejected without running an acquisition batch;
   retain only redacted success/failure evidence. Test the new credentials with
   the established authorization checks, then separately authorize wake.

No automatic rotation, expiry extension, or account mutation is implemented.

## Clean source lineage proposal — approval required before execution

Prefer a reviewed source-only independent lineage, prepared in an isolated
checkout with a file allowlist. Do not copy the existing `.git` directory. Include
source, migrations, manifests whose content is safe, dependency locks, tests and
operator documentation. Audit operational manifests for private URLs, tokens,
secret values, and machine-specific configuration before including them.

Exclude credentials (`.env*`, `.screener*`, cookies, browser profiles, auth state),
SQLite files/sidecars, database snapshots, PDF/document archives and extracted
private content, runtime receipts/logs/queues, local locks, temporary files,
node_modules/venvs/builds, OCI images, ZIP/TAR exports and unnecessary binaries.
`.gitignore` now excludes common additions; it does **not** untrack existing
files or repair the extensively staged index. Never commit that index wholesale.
Screenshots here are deliberately generated deliverables, not document archives;
review them for private company/operational information before source publication.

Produce an explicit included/excluded manifest and scan every proposed reachable
commit and file for secrets and large/binary content. Review the clean snapshot
before approving its repository/branch destination. A normal non-force push of
an independent clean branch may be considered after review; it does not sanitize
other existing refs. If historical rewriting becomes necessary, obtain explicit
approval for its exact ref set, recovery copies, collaborator coordination and
force push. This task grants none of that approval.

After any approved clean backup, verify the remote full commit SHA and perform a
fresh clone/restore test with scheduler/network disabled. Record the clean branch
and commit in operator docs. There is currently no new verified GitHub backup.

## Financial data: separate, recoverable backups

GitHub is a source-code backup only. The production database is
`trading-ai_scraper-data:/data/scraper.db`, distinct from the host
`scrapper/store/scraper.db`. Original PDFs and associated content live under
`/Volumes/My Passport/passport/trading-ai`. Neither is backed up by this task.

Follow the detailed seven-step backup/restore procedure in
[operational recovery](operational-recovery-20261022.md#separate-database-documents-configuration-and-state-backup-plan).
Use a second physical device plus encrypted offsite storage. During a guarded
pause, export SQLite consistently using its backup protocol after ownership/WAL
checks. Do not copy a live main database file or delete/checkpoint sidecars to
make a copy appear consistent. Inventory snapshots/exports and the separate host
database independently.

Copy original documents resumably without deletion, preserve metadata, and verify
hashes against database content identities. Tie the PDF manifest to the database
snapshot with timestamps, counts, byte sizes and checksums. Encrypt secrets and
configuration separately with restricted access. Preserve receipts, authorization
and stop-latch evidence without treating stale PIDs/locks as restart instructions.

Restore-test the database into an isolated volume with no scheduler/network;
run integrity/schema/count checks on the restored copy and verify representative
PDF lineage/hashes. Maintain daily versions during reporting, weekly retention,
and an encrypted offsite copy. Regularly repeat a full recovery test. A backup
without a successful restore verification remains unknown, never healthy.
