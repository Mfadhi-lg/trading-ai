-- M05 auditable consecutive accepted scheme-month comparisons.
CREATE TABLE IF NOT EXISTS mf_scheme_month_change_sets (
    change_set_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    previous_month TEXT NOT NULL,
    current_month TEXT NOT NULL,
    previous_import_file_id INTEGER REFERENCES mf_import_files(import_file_id),
    current_import_file_id INTEGER REFERENCES mf_import_files(import_file_id),
    comparison_status TEXT NOT NULL CHECK (
      comparison_status IN ('generated', 'skipped')
    ),
    reason_code TEXT NOT NULL,
    completeness_evidence_json TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL UNIQUE,
    generated_at_utc TEXT NOT NULL,
    CHECK (previous_month < current_month),
    CHECK (
      comparison_status = 'skipped'
      OR (previous_import_file_id IS NOT NULL AND current_import_file_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_mf_change_sets_pair
ON mf_scheme_month_change_sets(
  publisher, scheme_id, previous_month, current_month, generated_at_utc
);

CREATE TABLE IF NOT EXISTS mf_scheme_month_change_validations (
    change_set_id INTEGER NOT NULL
        REFERENCES mf_scheme_month_change_sets(change_set_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    validation_status TEXT NOT NULL CHECK (
      validation_status IN ('passed', 'failed', 'not_evaluated')
    ),
    details_json TEXT NOT NULL,
    PRIMARY KEY(change_set_id, rule_code)
);

CREATE TABLE IF NOT EXISTS mf_scheme_security_month_changes (
    scheme_security_change_id INTEGER PRIMARY KEY,
    change_set_id INTEGER NOT NULL
        REFERENCES mf_scheme_month_change_sets(change_set_id),
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    security_id INTEGER NOT NULL REFERENCES mf_securities(security_id),
    isin_normalized TEXT NOT NULL,
    previous_month TEXT NOT NULL,
    current_month TEXT NOT NULL,
    previous_holding_id INTEGER REFERENCES mf_holdings(holding_id),
    current_holding_id INTEGER REFERENCES mf_holdings(holding_id),
    previous_row_presence TEXT NOT NULL CHECK (
      previous_row_presence IN ('present', 'absent')
    ),
    current_row_presence TEXT NOT NULL CHECK (
      current_row_presence IN ('present', 'absent')
    ),
    previous_quantity_decimal TEXT,
    current_quantity_decimal TEXT,
    quantity_change_decimal TEXT NOT NULL,
    previous_weight_decimal TEXT,
    current_weight_decimal TEXT,
    portfolio_weight_change_decimal TEXT NOT NULL,
    change_class TEXT NOT NULL CHECK (
      change_class IN (
        'new_entry', 'complete_exit', 'quantity_increase',
        'quantity_decrease', 'unchanged'
      )
    ),
    previous_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    current_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    completeness_evidence_json TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    UNIQUE(change_set_id, security_id),
    CHECK (
      (previous_row_presence='present' AND previous_holding_id IS NOT NULL
       AND previous_quantity_decimal IS NOT NULL AND previous_weight_decimal IS NOT NULL)
      OR
      (previous_row_presence='absent' AND previous_holding_id IS NULL
       AND previous_quantity_decimal IS NULL AND previous_weight_decimal IS NULL)
    ),
    CHECK (
      (current_row_presence='present' AND current_holding_id IS NOT NULL
       AND current_quantity_decimal IS NOT NULL AND current_weight_decimal IS NOT NULL)
      OR
      (current_row_presence='absent' AND current_holding_id IS NULL
       AND current_quantity_decimal IS NULL AND current_weight_decimal IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_mf_scheme_security_changes_month
ON mf_scheme_security_month_changes(
  publisher, scheme_id, current_month, security_id
);

-- Only comparison sets whose two import IDs still match both accepted pointers
-- are current. Superseded change rows remain append-only historical evidence.
CREATE VIEW IF NOT EXISTS mf_current_scheme_month_change_sets AS
SELECT cs.*
FROM mf_scheme_month_change_sets AS cs
JOIN mf_accepted_versions AS previous_version
  ON previous_version.publisher=cs.publisher
 AND previous_version.scheme_id=cs.scheme_id
 AND previous_version.disclosure_month=cs.previous_month
 AND previous_version.import_file_id=cs.previous_import_file_id
JOIN mf_accepted_versions AS current_version
  ON current_version.publisher=cs.publisher
 AND current_version.scheme_id=cs.scheme_id
 AND current_version.disclosure_month=cs.current_month
 AND current_version.import_file_id=cs.current_import_file_id
WHERE cs.comparison_status='generated'
  AND cs.change_set_id=(
    SELECT MAX(newest.change_set_id)
    FROM mf_scheme_month_change_sets AS newest
    WHERE newest.publisher=cs.publisher
      AND newest.scheme_id=cs.scheme_id
      AND newest.previous_month=cs.previous_month
      AND newest.current_month=cs.current_month
      AND newest.previous_import_file_id=cs.previous_import_file_id
      AND newest.current_import_file_id=cs.current_import_file_id
      AND newest.comparison_status='generated'
  );

CREATE VIEW IF NOT EXISTS mf_current_scheme_security_month_changes AS
SELECT changes.*
FROM mf_scheme_security_month_changes AS changes
JOIN mf_current_scheme_month_change_sets AS current_sets
  ON current_sets.change_set_id=changes.change_set_id;
