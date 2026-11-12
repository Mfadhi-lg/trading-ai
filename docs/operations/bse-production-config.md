# BSE production configuration

Scheduled BSE acquisition loads its image and application hashes through
`ops/activation/bse_production.py`. Native PostgreSQL releases use their verified
`ops/activation/postgresql-target.json`. SQLite releases use
`ops/activation/bse-production.json`, pinned by the acquisition manifest. Missing
or invalid native configuration cannot fall back to SQLite configuration.

The SQLite image identity is the existing `phase4_documents.IMAGE`, also recorded
in the retained 9 October ownership-fixed build log. Its application inventory
uses the same AR-02 period source manifest and browser adapter hash used by the
original BSE packaging. No historical operation manifest is needed at runtime.
Captured browser responses and execution receipts retain their evidence paths.

Source changes do not update an active release. Deployment must include the new
helper and refresh the native source inventory and acquisition manifest before
activation. Retained STOP records require a separately authorized recovery;
consumed slots must not be replayed. Ownership ambiguity must be investigated,
not suppressed merely because the reported PID has exited.
