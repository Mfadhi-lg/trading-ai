-- M09 third-party mfdata evidence and official-first reconciliation.
-- Third-party observations are physically separate from official workbook rows;
-- only the preferred view may expose them as a fallback.
CREATE TABLE IF NOT EXISTS mfdata_snapshots (
    mfdata_snapshot_id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'mfdata'),
    source TEXT NOT NULL CHECK (source = 'mfdata'),
    source_quality TEXT NOT NULL CHECK (
      source_quality = 'third_party_unverified'
    ),
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    scheme_code TEXT NOT NULL,
    provider_family_id INTEGER NOT NULL CHECK (provider_family_id > 0),
    requested_disclosure_month TEXT NOT NULL,
    detected_disclosure_month TEXT NOT NULL,
    response_url TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    submitted_path TEXT NOT NULL,
    immutable_path TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status = 'accepted'),
    first_observed_at_utc TEXT NOT NULL,
    accepted_at_utc TEXT NOT NULL,
    UNIQUE(provider, content_sha256)
);

CREATE TABLE IF NOT EXISTS mfdata_snapshot_observations (
    mfdata_observation_id INTEGER PRIMARY KEY,
    mfdata_snapshot_id INTEGER NOT NULL
        REFERENCES mfdata_snapshots(mfdata_snapshot_id),
    observed_at_utc TEXT NOT NULL,
    submitted_path TEXT NOT NULL,
    observation_result TEXT NOT NULL CHECK (
      observation_result IN ('accepted', 'duplicate')
    )
);

CREATE TABLE IF NOT EXISTS mfdata_holdings (
    mfdata_holding_id INTEGER PRIMARY KEY,
    mfdata_snapshot_id INTEGER NOT NULL
        REFERENCES mfdata_snapshots(mfdata_snapshot_id) ON DELETE CASCADE,
    source_position INTEGER NOT NULL CHECK (source_position >= 0),
    holding_type TEXT NOT NULL CHECK (
      holding_type IN ('equity', 'debt', 'other')
    ),
    holding_type_position INTEGER NOT NULL CHECK (holding_type_position >= 0),
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    scheme_code TEXT NOT NULL,
    provider_family_id INTEGER NOT NULL CHECK (provider_family_id > 0),
    disclosure_month TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source = 'mfdata'),
    source_quality TEXT NOT NULL CHECK (
      source_quality = 'third_party_unverified'
    ),
    security_name_raw TEXT NOT NULL,
    isin_raw TEXT,
    isin_normalized TEXT,
    isin_validation_status TEXT NOT NULL CHECK (
      isin_validation_status IN (
        'valid', 'missing', 'invalid_format', 'invalid_checksum'
      )
    ),
    quantity_display TEXT,
    quantity_decimal TEXT,
    market_value_cr_display TEXT,
    market_value_cr_decimal TEXT,
    market_value_lakh_decimal TEXT,
    percentage_of_nav_display TEXT,
    percentage_of_nav_decimal TEXT,
    change_mom_display TEXT,
    change_mom_decimal TEXT,
    currency TEXT NOT NULL CHECK (currency = 'INR'),
    quantity_unit_code TEXT NOT NULL CHECK (quantity_unit_code = 'COUNT'),
    market_value_source_unit_code TEXT NOT NULL CHECK (
      market_value_source_unit_code = 'INR_CRORE'
    ),
    market_value_normalized_unit_code TEXT NOT NULL CHECK (
      market_value_normalized_unit_code = 'INR_LAKH'
    ),
    percentage_unit_code TEXT NOT NULL CHECK (
      percentage_unit_code = 'PERCENT_POINT'
    ),
    source_row_json TEXT NOT NULL,
    security_id INTEGER REFERENCES mf_securities(security_id),
    security_match_status TEXT NOT NULL CHECK (
      security_match_status IN ('matched', 'unmatched', 'invalid_isin')
    ),
    normalization_rule_version TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    accepted_at_utc TEXT NOT NULL,
    UNIQUE(mfdata_snapshot_id, source_position),
    UNIQUE(mfdata_snapshot_id, holding_type, holding_type_position)
);

CREATE INDEX IF NOT EXISTS ix_mfdata_holdings_lookup
ON mfdata_holdings(publisher, scheme_code, disclosure_month, isin_normalized);

CREATE TABLE IF NOT EXISTS mfdata_accepted_versions (
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    disclosure_month TEXT NOT NULL,
    mfdata_snapshot_id INTEGER NOT NULL
        REFERENCES mfdata_snapshots(mfdata_snapshot_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    PRIMARY KEY(publisher, scheme_id, disclosure_month)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS mfdata_accepted_version_events (
    mfdata_version_event_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    disclosure_month TEXT NOT NULL,
    mfdata_snapshot_id INTEGER NOT NULL
        REFERENCES mfdata_snapshots(mfdata_snapshot_id),
    previous_mfdata_snapshot_id INTEGER
        REFERENCES mfdata_snapshots(mfdata_snapshot_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    UNIQUE(publisher, scheme_id, disclosure_month, mfdata_snapshot_id)
);

CREATE TABLE IF NOT EXISTS mfdata_crosscheck_runs (
    mfdata_crosscheck_run_id INTEGER PRIMARY KEY,
    mfdata_snapshot_id INTEGER NOT NULL
        REFERENCES mfdata_snapshots(mfdata_snapshot_id),
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    disclosure_month TEXT NOT NULL,
    comparison_status TEXT NOT NULL CHECK (
      comparison_status IN ('passed', 'passed_with_warnings')
    ),
    official_observation_count INTEGER NOT NULL CHECK (
      official_observation_count >= 0
    ),
    third_party_observation_count INTEGER NOT NULL CHECK (
      third_party_observation_count >= 0
    ),
    matched_count INTEGER NOT NULL CHECK (matched_count >= 0),
    conflict_count INTEGER NOT NULL CHECK (conflict_count >= 0),
    official_only_count INTEGER NOT NULL CHECK (official_only_count >= 0),
    third_party_only_count INTEGER NOT NULL CHECK (third_party_only_count >= 0),
    unmatched_third_party_count INTEGER NOT NULL CHECK (
      unmatched_third_party_count >= 0
    ),
    official_precedence_enforced INTEGER NOT NULL CHECK (
      official_precedence_enforced = 1
    ),
    input_fingerprint TEXT NOT NULL UNIQUE,
    generated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mfdata_crosscheck_results (
    mfdata_crosscheck_result_id INTEGER PRIMARY KEY,
    mfdata_crosscheck_run_id INTEGER NOT NULL
        REFERENCES mfdata_crosscheck_runs(mfdata_crosscheck_run_id)
        ON DELETE CASCADE,
    result_position INTEGER NOT NULL CHECK (result_position >= 0),
    result_status TEXT NOT NULL CHECK (
      result_status IN (
        'match', 'conflict', 'official_only', 'third_party_only',
        'unmatched_third_party'
      )
    ),
    security_id INTEGER REFERENCES mf_securities(security_id),
    isin_normalized TEXT,
    security_name_raw TEXT NOT NULL,
    official_holding_ids_json TEXT NOT NULL,
    mfdata_holding_id INTEGER REFERENCES mfdata_holdings(mfdata_holding_id),
    official_quantity_decimal TEXT,
    mfdata_quantity_decimal TEXT,
    official_market_value_lakh_decimal TEXT,
    mfdata_market_value_lakh_decimal TEXT,
    official_percentage_of_nav_decimal TEXT,
    mfdata_percentage_of_nav_decimal TEXT,
    conflict_fields_json TEXT NOT NULL,
    preferred_source TEXT CHECK (
      preferred_source IS NULL OR preferred_source IN ('amc_workbook', 'mfdata')
    ),
    preferred_source_quality TEXT CHECK (
      preferred_source_quality IS NULL
      OR preferred_source_quality IN ('official', 'third_party_unverified')
    ),
    UNIQUE(mfdata_crosscheck_run_id, result_position)
);

-- Only explicitly accepted mfdata snapshots are visible downstream.
CREATE VIEW IF NOT EXISTS mf_current_mfdata_holdings AS
SELECT holding.*
FROM mfdata_holdings AS holding
JOIN mfdata_accepted_versions AS accepted
  ON accepted.publisher=holding.publisher
 AND accepted.scheme_id=holding.scheme_id
 AND accepted.disclosure_month=holding.disclosure_month
 AND accepted.mfdata_snapshot_id=holding.mfdata_snapshot_id;

-- This evidence surface makes precedence inspectable. Official rows always have
-- rank 1 and remain preferred. A valid, security-resolved mfdata row receives
-- rank 2 and becomes preferred only when the exact official key is absent.
CREATE VIEW IF NOT EXISTS mf_holding_source_evidence AS
SELECT
  'official:' || holding.holding_id AS observation_key,
  holding.holding_id AS source_observation_id,
  holding.publisher,
  holding.scheme_id,
  holding.scheme_code,
  holding.disclosure_month,
  holding.security_id,
  holding.security_name_raw,
  holding.isin_normalized,
  holding.quantity_decimal,
  holding.market_value_decimal AS market_value_lakh_decimal,
  holding.percentage_of_nav_decimal,
  holding.source,
  holding.source_quality,
  1 AS source_precedence_rank,
  1 AS is_preferred
FROM mf_accepted_holdings AS holding
UNION ALL
SELECT
  'mfdata:' || holding.mfdata_holding_id AS observation_key,
  holding.mfdata_holding_id AS source_observation_id,
  holding.publisher,
  holding.scheme_id,
  holding.scheme_code,
  holding.disclosure_month,
  holding.security_id,
  holding.security_name_raw,
  holding.isin_normalized,
  holding.quantity_decimal,
  holding.market_value_lakh_decimal,
  holding.percentage_of_nav_decimal,
  holding.source,
  holding.source_quality,
  2 AS source_precedence_rank,
  CASE
    WHEN holding.security_id IS NULL
      OR holding.isin_validation_status != 'valid' THEN 0
    WHEN EXISTS (
      SELECT 1
      FROM mf_accepted_holdings AS official
      WHERE official.publisher=holding.publisher
        AND official.scheme_id=holding.scheme_id
        AND official.disclosure_month=holding.disclosure_month
        AND official.isin_normalized=holding.isin_normalized
    ) THEN 0
    ELSE 1
  END AS is_preferred
FROM mf_current_mfdata_holdings AS holding;

CREATE VIEW IF NOT EXISTS mf_preferred_holdings AS
SELECT *
FROM mf_holding_source_evidence
WHERE is_preferred=1;
