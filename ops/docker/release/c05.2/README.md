# C05.2 release-isolation candidate

This directory describes the prepared release identity used by `compose.yml`.
It does **not** authorize or perform production activation. C06 remains the
separate activation boundary.

Production Compose uses the digest-qualified C05.1 candidate image for every
role, contains no application-source bind mount and mounts the exact C05.0
runtime identity and data-volume sentinels as read-only Compose configs. The
ordinary roles use the core identity (`/data/snapshots` is their document
target); document-capable roles use the external-document identity
(`/documents`). The prepared `external-documents-sentinel.json` must be placed
at `/documents/.trading_ai_runtime_identity.json` during the separately
authorized C06 activation. It is intentionally not overmounted by Compose,
because the extraction role preserves its read-only document mount. Existing
data, document, secret, resource and profile semantics remain in place.

The release manifest records both the local repository digest used by Compose
and the C05.1 platform/config digests. C05.3 must reproduce and export the image
before C06 can consider activation.

For isolated development, explicitly add the development override:

```sh
docker compose -f compose.yml -f compose.development.yml --profile manual config
```

The override uses Compose's `!override` tag and therefore requires Docker
Compose 2.24.4 or newer (verified here with 5.5.1).

It replaces production data mounts with the `development-data` and
`development-documents` volumes at `/development-data` and
`/development-documents`, removes production identity configs and adds the
source bind mount. The non-production paths are deliberate: C05.0 rejects
`/data` and `/documents` for development writers even when Docker would attach
a different named volume.

Browser campaign development uses its matching pair:

```sh
docker compose \
  -f compose.browser-recovery.yml \
  -f compose.browser-recovery.development.yml \
  --profile browser-recovery config
```

Run `python3 scripts/audit/check_compose_release.py` to validate all 16 main
roles and all four browser roles in both production and development forms.
