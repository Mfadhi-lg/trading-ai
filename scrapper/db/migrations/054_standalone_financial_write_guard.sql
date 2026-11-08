-- Database-level backstop: no future financial time-series row can be written
-- under any scope other than the actual standalone statement.  Historical rows
-- are intentionally not rewritten; 053 exposes them for audit/quarantine.
CREATE TRIGGER IF NOT EXISTS trg_financial_observations_standalone_only
BEFORE INSERT ON financial_observations
WHEN NEW.reporting_scope <> 'standalone'
BEGIN
  SELECT RAISE(ABORT, 'financial observations require reporting_scope=standalone');
END;

CREATE TRIGGER IF NOT EXISTS trg_financial_observations_standalone_only_update
BEFORE UPDATE OF reporting_scope ON financial_observations
WHEN NEW.reporting_scope <> 'standalone'
BEGIN
  SELECT RAISE(ABORT, 'financial observations require reporting_scope=standalone');
END;
