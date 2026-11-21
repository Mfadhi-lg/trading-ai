# ACQ-RELEASE-READY-01 — release preparation

The retained ACQ-RESTORE-01 inspection established that both financial diagnostics
raised IndexError before argument parsing at Docker's `/host/script.py` depth.
They now find an ancestor containing `scrapper`, with `/app` as the mounted
fallback. Direct `acquisition_queries.py` remains in the sealed reader profile;
its path setup now precedes native imports. The live-reader wrapper is preserved.

The financial screen capture ceiling is 39 pages, matching the separately
observed growth beyond 37. This is a software ceiling, not a production request
grant. Installed budgets, daily limits (148), source identity, UTC-day expiration,
redirect rejection, durable reservations and complete-page admission are
unchanged. A smaller grant still rejects a 39-page capture before page 2.
Counts, page numbers, query and distinct source company identities must agree
before any queue admission. Interrupted or partial captures retain evidence and
reservations and admit no jobs. No production grant is installed by this change.

Synthetic regression coverage exercises 1,933 rows across 39 pages, idempotent
admission, all four insufficient grant fields, final-page inconsistency,
duplicates, missing bytes and interruption. The required native PostgreSQL smoke
reuses these same contracts on disposable schemas in addition to the retained
37-page cases. Synthetic counts do not certify current publisher results.

Real mounted-depth startup coverage is explicit and opt-in:

```sh
ACQ_TEST_IMAGE=sha256:IMAGE python -B -m pytest -q ops/host/test_mounted_diagnostics.py
```

It mounts only source at `/host` and `/activation`, uses network none and imports
the supported query entry points without executing queries. Run it again from
the archived final reviewed source against the exact candidate image. Candidate
source/image/manifest inventories, GitHub checks, review, preservation evidence
and proposed canaries are retained separately under
`artifacts/automatic-acquisition/acq-release-ready-01/`. Preparation grants no
production deployment, stop resolution or canary execution permission.
