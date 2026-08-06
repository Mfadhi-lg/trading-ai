-- Preserve the source's click-to-expand schedule marker on statement rows
-- (balance sheet, and the same drill-down control on quarters/P&L/cash flow).
-- The expanded schedule dataset itself remains a deferred component; only the
-- marker and its schedule key are captured in Phase 1.
ALTER TABLE financial_observations ADD COLUMN is_expandable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE financial_observations ADD COLUMN schedule_key TEXT;
