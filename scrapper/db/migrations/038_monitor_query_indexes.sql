-- The live monitor reads queue events in per-item timestamp order.  Without
-- this index, its average-duration statistic requires a full temporary sort
-- of an ever-growing event history.
CREATE INDEX IF NOT EXISTS idx_screener_queue_events_item_time
ON screener_company_queue_events(queue_item_id, event_at_utc, queue_event_id);

-- Supports the active/recent monitor panes without scanning unrelated queue
-- statuses on every refresh.
CREATE INDEX IF NOT EXISTS idx_screener_queue_status_updated
ON screener_company_queue(status, updated_at_utc DESC);
