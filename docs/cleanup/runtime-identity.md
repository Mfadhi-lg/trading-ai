# Runtime write identity contract

Every writer requires `TRADING_AI_ENV=production|development|test` before it
creates directories, opens a writable SQLite connection, probes the document
store, publishes host capacity, or starts the browser worker. Read-only database
connections, imports and help remain inert.

Production additionally requires `TRADING_AI_RUNTIME_IDENTITY_FILE`, a reviewed
JSON file with `authorized: true`, `release_id`, `git_commit`, `image_id`, and
exact resolved `targets` for `db`, `store`, and `documents`. Store and document
roots each carry `.trading_ai_runtime_identity.json` with the same release ID,
resolved path, environment and role. Optional `additional_writable_roots` are
exact roots for host runtime outputs; there is no general bypass switch.

Development and test reject known production paths, Docker production mount
identities, and production markers. A restored clone that contains a copied
production marker requires `TRADING_AI_CLONE_IDENTITY_FILE`: its environment,
unique clone ID, exact resolved clone root, and SHA256 of that marker must match
the destination. This authorizes only paths inside that clone.

Successful checks log resolved targets and public release, Git, image and clone
identifiers to stderr. Identity files must contain no credentials.
