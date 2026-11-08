"""Cold read-only Phase 3 snapshot after the existing empty-WAL guard."""
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot
from scrapper.db.database import check_schema_compatibility
p=Path('/data/scraper.db'); before=assert_safe_empty_wal(p)
# Immutable is valid only after proving no pending WAL or connection; no sidecar writes.
c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True); c.row_factory=sqlite3.Row
check_schema_compatibility(c)
now=datetime.now(timezone.utc).isoformat()
def q(sql,args=()):return [dict(r) for r in c.execute(sql,args)]
heg=q("SELECT * FROM screener_collection_scope WHERE company_path LIKE '%HEG%'")
ids=[r['company_id'] for r in heg if r['company_id']]
data={'observed_at_utc':now,'guard':before,'overdue':q('SELECT count(*) n FROM screener_collection_scope WHERE next_check_at_utc<=?',(now,)),
'cap_fetches':q("SELECT f.fetch_status,count(*) n,count(DISTINCT json_extract(r.config_json,'$.path')) companies FROM fetch_observations f JOIN scrape_runs r USING(run_id) WHERE f.resource_kind='market_cap_check' AND f.fetched_at_utc>='2026-10-09' GROUP BY 1"),
'cap_failures':q('SELECT count(*) n,sum(check_failures) attempts FROM screener_collection_scope WHERE check_failures>0'),
'cap_observations':q("SELECT count(*) n,count(DISTINCT company_path) companies FROM screener_scope_observations WHERE observed_at_utc>='2026-10-09'"),
'reentries':q("SELECT count(*) n FROM screener_company_queue_events WHERE json_extract(evidence_json,'$.reason')='market_cap_reentry' AND event_at_utc>='2026-10-09'"),
'confirmed_eligibility_revivals':q("SELECT count(DISTINCT a.scope_path) n FROM screener_company_queue_events e JOIN screener_company_queue q USING(queue_item_id) JOIN screener_scope_aliases a ON a.company_path=q.company_path_normalized WHERE json_extract(e.evidence_json,'$.reason')='market_cap_reentry' AND e.event_at_utc>='2026-10-09'"),
'recorded_transitions':q("SELECT count(*) n FROM screener_scope_observations WHERE eligibility='eligible' AND json_extract(evidence_json,'$.previous_eligibility') IN ('below_floor','unknown') AND observed_at_utc>='2026-10-09'"),
'prior_scope_observation_history':q("SELECT count(*) n,min(observed_at_utc) oldest FROM screener_scope_observations WHERE observed_at_utc<'2026-10-09'"),
'cap_latest_batch':q("SELECT company_path,eligibility,market_cap_crore,evidence_json FROM screener_scope_observations WHERE observed_at_utc>='2026-10-11T13:30:00+00:00' AND observed_at_utc<'2026-10-11T13:34:00+00:00'"),
'eligible_historical_preview':[],
'selected_queues':q('SELECT * FROM screener_company_queue WHERE queue_item_id IN (18126,18127,18128,18129,18130,18131,18132,18133,18137,18138)'),
'retained_acceptances':q("SELECT * FROM accepted_page_versions WHERE company_id IN (1,3,2822,3085) AND source='screener' AND reporting_scope='standalone'"),
'new_financial_observations':q("SELECT parse_id,section_code,count(*) n FROM financial_observations WHERE parse_id IN (34427,34429,34431,34433,34435,34437) GROUP BY 1,2"),
'heg_historical_cap':q("SELECT value_decimal FROM company_metric_observations WHERE parse_id=725 AND metric_code='market_cap'"),
'heg':heg,'heg_identifiers':q('SELECT * FROM source_identifiers WHERE company_id IN ('+','.join('?' for _ in ids)+')',ids) if ids else [],
'heg_security':q("SELECT * FROM exchange_securities WHERE nse_symbol='HEG' OR bse_scrip_code='509631'"),
'claims':q('SELECT company_path,claim_expires_at_utc FROM screener_collection_scope WHERE claim_token IS NOT NULL'),
'queues':q("SELECT origin,result_period,status,collection_disposition,count(*) n FROM screener_company_queue GROUP BY 1,2,3,4"),
'fresh_due':q("SELECT q.* FROM screener_company_queue q WHERE status IN ('pending','failed_retryable') AND collection_disposition IS NULL AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc<=?) ORDER BY result_period DESC,queue_item_id LIMIT 12",(now,)),
'revision_holds':q("SELECT count(*) n FROM exchange_screener_enqueues WHERE decision_reason LIKE 'possible_same_period_revision:%'"),
'holds':q('SELECT * FROM exchange_source_access_events'),
'host':q('SELECT * FROM acquisition_host_state')}
from financial_priority import select
rows,lanes,period=select(c,now_utc=now,activated_at='2026-10-09T00:00:00+00:00',historical_slot=True)
data['eligible_historical_preview']=[r.to_dict() for r,lane in zip(rows,lanes) if lane==3]
data['reserved_slot_selection']={'priorities':lanes,'period':period,'selected':[r.to_dict() for r in rows]}
c.close()
if snapshot(p)!=before['files']:raise RuntimeError('Production files changed during inspection')
print(json.dumps(data))
