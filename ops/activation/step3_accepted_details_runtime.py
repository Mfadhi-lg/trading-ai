"""Query-only accepted-quarter and validation evidence for the completed retry."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys


def main():
    with closing(sqlite3.connect('file:/data/scraper.db?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
        def rows(sql,args=()):return [dict(r) for r in c.execute(sql,args)]
        ids=tuple(r['accepted_parse_id'] for r in c.execute('SELECT accepted_parse_id FROM screener_company_queue WHERE queue_item_id IN (18126,18127) ORDER BY queue_item_id'))
        if any(x is None for x in ids):raise RuntimeError('Selected acceptance is incomplete')
        result={'parse_ids':ids,
            'financial_counts':rows('SELECT parse_id,reporting_scope,section_code,count(*) AS count FROM financial_observations WHERE parse_id IN (?,?) GROUP BY 1,2,3',ids),
            'september_records':rows("SELECT parse_id,reporting_scope,section_code,line_item_code,line_item_raw,period_end,display_value,value_decimal,unit_code,scale_multiplier,first_observed_at_utc,available_at_utc FROM financial_observations WHERE parse_id IN (?,?) AND section_code='quarters' AND period_end='2026-09-30' ORDER BY parse_id,row_position",ids),
            'validation':rows('SELECT * FROM validation_results WHERE parse_id IN (?,?) ORDER BY parse_id,rule_code',ids),
            'quick_ratio_metrics':rows("SELECT parse_id,metric_code,label_raw,value_decimal,missing_kind FROM company_metric_observations WHERE parse_id IN (?,?) ORDER BY parse_id,position",ids),
            'source_fetches':rows("SELECT f.*,b.content_sha256,b.byte_size,b.local_path FROM fetch_observations f JOIN response_blobs b USING(blob_id) WHERE f.run_id IN (SELECT run_id FROM fetch_observations WHERE blob_id IN (SELECT blob_id FROM parse_artifacts WHERE parse_id IN (?,?)) AND resource_kind='company_page') ORDER BY fetch_id",ids)}
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
