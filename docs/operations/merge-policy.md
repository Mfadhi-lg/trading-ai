# Main branch merge policy

Changes to `main` must go through a pull request. Both required checks must
succeed on a branch that is up to date with `main`, including for repository
administrators:

- `migration-lineage` in [Migration lineage](../../.github/workflows/migration-lineage.yml)
  checks frozen migration identities, disposable ledger expectations, migration
  controls and source/archive drift.
- `offline-contract` in [ML readiness](../../.github/workflows/ml-readiness.yml)
  installs locked test dependencies, migrates a disposable database from zero,
  runs the offline suite, bounded processing and terminal regressions, and checks
  reporting/data-integrity contracts.

These are the job/check names, not the workflow display names. Both are bound to
the GitHub Actions app (ID `15368`), as observed on this repository. Keep job names
stable; coordinate any rename with a protection update so merges do not remain
blocked waiting for a check that no longer exists. Keep both workflows running
for every pull request without path filters.

The policy requires pull requests with zero mandatory approving reviews, allowing
the repository owner to merge their own work once CI passes. No user or app is
given a pull-request bypass. Administrator enforcement is enabled; force pushes
and deletion of `main` are disabled. Actor-based push restrictions are unset
because this is a personal repository; the pull-request requirement prevents
direct pushes. Merge commits, squash merges and rebases remain available under
the repository's existing merge settings.

## Configuration and verification

[main-branch-protection.json](../../.github/main-branch-protection.json) is the
reviewable request body for GitHub's
[update branch protection API](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection).
Committing it does not activate protection. An administrator must explicitly
apply it from the repository root:

```sh
gh api --method PUT repos/Mfadhi-lg/trading-ai/branches/main/protection \
  --input .github/main-branch-protection.json
```

This replaces the branch protection configuration. Inspect the existing settings
first and reconcile any additional restrictions before reapplying it. Do not
automatically apply it from CI or grant workflows administration permissions.

Verify the live settings and the pull request's checks:

```sh
gh api repos/Mfadhi-lg/trading-ai/branches/main/protection
gh api repos/Mfadhi-lg/trading-ai/rulesets
gh pr checks <PR-number> --required
```

Confirm `strict: true`, both check names and app IDs, `enforce_admins.enabled:
true`, a non-null `required_pull_request_reviews` with zero mandatory approvals,
and disabled force pushes/deletions. Before merging, ensure both required checks
pass for the current pull request revision; update the branch if `main` advances.

On 8 November 2026, before this change, the protection endpoint returned
`404 Branch not protected`, the ruleset list was empty, and `main` at
`1dbc9330112def35f1ef622d2da0090122ce8690` had successful runs for both checks from
app `15368`. This is repository-setting evidence, not runtime or production health
evidence.

The policy was applied on the same date and read back through the protection
endpoint: strict checks `migration-lineage` and `offline-contract` from app
`15368`, administrator enforcement and the pull-request requirement were enabled;
mandatory approvals were zero; force pushes and deletions were disabled. Future
configuration can drift; use the verification commands above for current state.

For recovery, fix or rerun a failing check and update the branch. Any emergency
weakening of protection requires explicit owner authorization; restore this
policy and verify the live API response afterward. No database migration,
deployment or acquisition activation is part of this policy.
