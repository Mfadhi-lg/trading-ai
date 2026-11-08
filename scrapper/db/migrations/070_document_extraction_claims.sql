-- C19.3 ownership only: no enqueue, corpus selection or processing campaign.
CREATE TABLE document_extraction_claims (
    resource_key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','RELEASED')),
    lease_expires_at_utc TEXT NOT NULL
);
