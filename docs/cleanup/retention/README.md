# Generated-output retention and build inputs

C07.3 defines retention and location policy; it does not move, delete, untrack or
regenerate outputs. [Current status](../../STATUS.md) classifies research and
campaign evidence; [C07.3](../chunks/C07.3.md) records verification and limits.

- [Location policy](C07.3-locations.json) identifies owners, current retained
  locations and future destinations that still require separate selection.
- [Artifact inventory summary](C07.3-files.json) covers all 24,691 present files
  in 20 artifact groups, including ignored recovery outputs. Each group records
  bytes, tracked count and a deterministic per-file inventory checksum. The
  exact private inventory and full source snapshot remain at the recorded
  `/private/tmp` location; these summaries alone cannot reconstruct payloads.

Keep original evidence and campaign scripts in place. There is no expiration or
authorized deletion. A later C26.4 proposal must enumerate selected files,
verify copies and hashes at an operator-selected durable destination, prove
restoration/reference access and retain lineage before relocation/untracking.
C17/C18 own extraction of reusable campaign mutation logic. Historical research,
review-only source and superseded B2 material retain their existing dispositions.

Local backup, OCI and source captures under `/private/tmp` still need durable
retention; no offsite copy or new durable destination is claimed. C06's active
release configuration must remain available. Existing absent share ZIPs were
not restored or changed. Live stores and credential values are outside this
artifact inventory and are neither copied nor published by C07.3.

## Canonical Docker build context

The root [Dockerfile](../../../Dockerfile) copies `scrapper/requirements.txt`
and `scrapper/`; the root [.dockerignore](../../../.dockerignore) now allows
only that package and filters local environments, store/state, session secrets,
SQLite files, logs and caches. Runtime Python, both dependency manifests, all
67 migration files, source manifests, the rating identity catalog and 48 fixture
files remain byte/mode identical in Docker's actual exported context. The
fixture generator's cached `.pyc` is excluded; its source remains.

Root reports, artifacts, review trees, experiments, Git metadata, credentials,
Compose and host scripts are excluded. This rule governs the canonical root
Dockerfile only: a future Dockerfile that copies additional inputs must amend
and verify the allowlist first. Docker still reads build-control files; those
are not copied into the image by this policy.

Verification uses a `FROM scratch` / `COPY . /context/` build and local export,
plus synthetic nested exclusion probes. It does not install dependencies, build
a release, start workers or validate live runtime/data health. Existing images
and C06's separately retained active release configuration are unchanged.
