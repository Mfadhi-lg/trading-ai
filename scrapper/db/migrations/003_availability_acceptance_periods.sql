BEGIN IMMEDIATE;

ALTER TABLE financial_observations
RENAME COLUMN source_published_at TO source_published_at_utc;

CREATE TABLE company_metric_observations_new (
    metric_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    metric_code TEXT,
    label_raw TEXT NOT NULL,
    position INTEGER NOT NULL,
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    value_json TEXT,
    unit_code TEXT,
    scale_multiplier TEXT,
    missing_kind TEXT,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, position)
);

INSERT INTO company_metric_observations_new (
    metric_observation_id, parse_id, reporting_scope, metric_code, label_raw,
    position, display_value, value_decimal, value_json, unit_code,
    scale_multiplier, missing_kind, first_observed_at_utc,
    source_published_at_utc, available_at_utc, availability_basis
)
SELECT cmo.metric_observation_id, cmo.parse_id, cmo.reporting_scope,
       cmo.metric_code, cmo.label_raw, cmo.position, cmo.display_value,
       cmo.value_decimal, cmo.value_json, cmo.unit_code, cmo.scale_multiplier,
       cmo.missing_kind, rb.first_observed_at_utc, NULL,
       rb.first_observed_at_utc, 'first_observed'
FROM company_metric_observations AS cmo
JOIN parse_artifacts AS pa ON pa.parse_id = cmo.parse_id
JOIN response_blobs AS rb ON rb.blob_id = pa.blob_id;

DROP TABLE company_metric_observations;
ALTER TABLE company_metric_observations_new RENAME TO company_metric_observations;

CREATE TABLE quote_observations_new (
    parse_id INTEGER PRIMARY KEY REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    price_display_value TEXT NOT NULL,
    price_decimal TEXT,
    currency TEXT,
    quote_timestamp_raw TEXT,
    quote_timestamp_utc TEXT,
    quote_timezone TEXT,
    quote_timestamp_status TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL
);

INSERT INTO quote_observations_new (
    parse_id, reporting_scope, price_display_value, price_decimal, currency,
    quote_timestamp_raw, quote_timestamp_utc, quote_timezone,
    quote_timestamp_status, first_observed_at_utc
)
SELECT qo.parse_id,
       COALESCE(
         (SELECT cmo.reporting_scope FROM company_metric_observations AS cmo
          WHERE cmo.parse_id = qo.parse_id LIMIT 1),
         'not_applicable'
       ),
       qo.display_price, qo.price_decimal, qo.currency,
       qo.quote_timestamp_raw, qo.quote_timestamp_utc, qo.quote_timezone,
       qo.quote_timestamp_status, rb.first_observed_at_utc
FROM quote_observations AS qo
JOIN parse_artifacts AS pa ON pa.parse_id = qo.parse_id
JOIN response_blobs AS rb ON rb.blob_id = pa.blob_id;

DROP TABLE quote_observations;
ALTER TABLE quote_observations_new RENAME TO quote_observations;

CREATE TABLE fetch_observations_new (
    fetch_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES scrape_runs(run_id),
    company_id INTEGER REFERENCES companies(company_id),
    source TEXT NOT NULL,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable',
    requested_source_period TEXT NOT NULL DEFAULT 'not_applicable',
    resolved_source_period TEXT NOT NULL DEFAULT 'not_applicable',
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    etag TEXT,
    last_modified TEXT,
    auth_mode TEXT NOT NULL,
    blob_id INTEGER REFERENCES response_blobs(blob_id),
    fetch_status TEXT NOT NULL CHECK (
      fetch_status IN ('ok', 'not_modified', 'rejected', 'error')
    )
);

INSERT INTO fetch_observations_new (
    fetch_id, run_id, company_id, source, reporting_scope,
    requested_source_period, resolved_source_period, requested_url, final_url,
    fetched_at_utc, http_status, etag, last_modified, auth_mode, blob_id,
    fetch_status
)
SELECT fetch_id, run_id, company_id, source,
       COALESCE(NULLIF(reporting_scope, ''), 'not_applicable'),
       COALESCE(NULLIF(source_period, ''), 'not_applicable'),
       COALESCE(NULLIF(source_period, ''), 'not_applicable'),
       requested_url, final_url, fetched_at_utc, http_status, etag,
       last_modified, auth_mode, blob_id, fetch_status
FROM fetch_observations;

DROP TABLE fetch_observations;
ALTER TABLE fetch_observations_new RENAME TO fetch_observations;

CREATE TABLE acceptance_events (
    acceptance_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    source TEXT NOT NULL,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable',
    resolved_source_period TEXT NOT NULL DEFAULT 'not_applicable',
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id),
    action TEXT NOT NULL CHECK (action IN ('accepted', 'superseded', 'rejected')),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    reason_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO acceptance_events (
    company_id, source, reporting_scope, resolved_source_period, parse_id,
    action, accepted_at_utc, accepted_by, acceptance_mode, reason_json
)
SELECT company_id, source,
       COALESCE(NULLIF(reporting_scope, ''), 'not_applicable'),
       COALESCE(NULLIF(source_period, ''), 'not_applicable'),
       parse_id, 'accepted', accepted_at_utc, accepted_by, acceptance_mode,
       '{"basis":"migration_003_current_pointer"}'
FROM accepted_page_versions;

DROP VIEW latest_accepted_pages;

CREATE TABLE accepted_page_versions_new (
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    source TEXT NOT NULL,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable',
    resolved_source_period TEXT NOT NULL DEFAULT 'not_applicable',
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    PRIMARY KEY(company_id, source, reporting_scope, resolved_source_period)
) WITHOUT ROWID;

INSERT INTO accepted_page_versions_new (
    company_id, source, reporting_scope, resolved_source_period, parse_id,
    accepted_at_utc, accepted_by, acceptance_mode
)
SELECT company_id, source,
       COALESCE(NULLIF(reporting_scope, ''), 'not_applicable'),
       COALESCE(NULLIF(source_period, ''), 'not_applicable'),
       parse_id, accepted_at_utc, accepted_by, acceptance_mode
FROM accepted_page_versions;

DROP TABLE accepted_page_versions;
ALTER TABLE accepted_page_versions_new RENAME TO accepted_page_versions;

CREATE VIEW latest_accepted_pages AS
SELECT apv.*, pa.blob_id, pa.parser_name, pa.parser_version,
       pa.manifest_version, pa.validation_status
FROM accepted_page_versions AS apv
JOIN parse_artifacts AS pa ON pa.parse_id = apv.parse_id;

COMMIT;
