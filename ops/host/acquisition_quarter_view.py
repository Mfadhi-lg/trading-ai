"""Rich quarter monitor within the existing terminal application."""
from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from reporting_quarters import label
from candle_monitor import summary_lines


def render_details(feed, inspection, view='all', compact=False):
    q = inspection.get('quarter', 'all')
    data = inspection.get('quarter_data') or {}
    title = label(q)
    def grid(name, columns, records):
        t = Table(title=name, expand=True, box=None)
        for c in columns:
            t.add_column(c)
        for row in records:
            t.add_row(*(Text(str(v) if v is not None else '—') for v in row))
        if not records:
            t.add_row('No records in this quarter', *['']*(len(columns)-1))
        return t
    parts = [Text('Reporting Quarter: ['+title+']',style='bold cyan'),
             Text('March FY convention for shared labels; explicit company fiscal metadata remains authoritative.',style='dim')]
    parts.extend(Text(value, style='cyan') for value in summary_lines(feed.get('historical_candles'), str))
    if inspection.get('quarter_error'):
        parts.append(Text('Refresh deferred: '+inspection['quarter_error'],style='yellow'))
    parts.append(Text('Database observed: '+str(inspection.get('quarter_at') or 'not yet observed')+' · r refresh · [ older · ] newer · a all · u unassigned · q quarter picker',style='dim'))
    if not data:
        parts.append(Text('Quarter data unavailable. Waiting for a guarded read; no lifetime counts substituted.'))
    else:
        counts = [(name,r['state'],r['n']) for name,rows in data['queues'].items() for r in rows]
        parts.append(grid('QUARTER QUEUES · job counts', ['Queue','State','Jobs'],counts))
        c = data['coverage']
        parts.append(Text('Lifetime master universe: '+str(c['universe'])+' · Currently eligible (all-quarter policy): '+str(c['eligible']),style='dim'))
        parts.append(Text('Selected quarter · Financial results found '+str(c['results_detected'])+' · official filing companies '+str(c['official_results_found'])+' · admitted '+str(c['admitted'])+' · Financials accepted from Screener: standalone '+str(c['standalone'])+' · consolidated '+str(c['consolidated'])+' · both scopes '+str(c['both_scopes'])+' · companies with stored documents '+str(c['with_stored_documents'])))
        parts.append(Text(data['discovery_coverage'],style='yellow'))
        ac=data.get('acquisition',{})
        if ac:
            parts.append(grid('DOCUMENT ACQUISITION · selected reporting quarter', ['Measure','Documents'],[
                ('Links discovered (canonical artifacts)',ac['document_links_discovered']),
                ('Already stored',ac['documents_already_stored']),
                ('New downloads since 9 Oct epoch',ac['new_documents_downloaded_since_epoch']),
                ('Awaiting download',ac['documents_awaiting_download']),
                ('Blocked by source policy',ac['documents_blocked_source_policy']),
                ('Awaiting publication (expectations)',ac['documents_awaiting_publication']),
                ('Processing completed',ac['document_processing_completed'])]))
            parts.append(Text('Quarter companies also have '+str(ac['unknown_period_documents_for_quarter_companies'])+
                ' documents with UNKNOWN reporting period. These are excluded from quarter document totals; zero associated documents does not mean no links exist.',style='yellow'))
            if view in ('downloads','ratings'):
                parts.append(grid('DOWNLOAD EXCLUSIONS · selected quarter', ['State','Reason','Documents','Eligibility'],[
                    (r['status'],r['reason'],r['documents'],r['eligibility']) for r in data.get('download_exclusions',[])]))
        if view in ('downloads','ratings'):
            parts.append(grid('DISCOVERY · selected quarter', ['Availability','Expectations'],[(r['state'],r['n']) for r in data['discovery']]))
            parts.append(grid('PROCESSING · selected quarter, preferred or latest stored contents',['State','Contents'],[(r['state'],r['n']) for r in data['processing']]))
            parts.append(Text('Last validated download: '+str(data['last_download']['at'] or 'none observed')+' · stored validation, bytes not rehashed by monitor'))
            parts.append(grid('DOCUMENTS · selected quarter · first 40, --offset for next page',
                ['Company','Type','Reported FY/Q','Download','Processing','Updated','Reason'],
                [(r['current_legal_name'],r['artifact_type'],f"{r['fiscal_year'] or '?'} / {r['fiscal_quarter'] or '?'}",r['status'] or r['acquisition_status'],r['extraction_status'],r['updated_at_utc'],r['last_error'])
                for r in data['downloads'] if view!='ratings' or r['artifact_type']=='credit_rating']))
        else:
            parts.append(grid('FINANCIAL WORK · selected quarter · most recent 40',
                ['Company','Scope','State','Updated','Reason'],[(r['company_path_normalized'],r['reporting_scope'],r['status'],r['updated_at_utc'],r['last_error_json']) for r in data['financial_jobs'][:6 if compact else 40]]))
            parts.append(grid('COMPANY ACQUISITION · selected quarter · --term filters, --offset pages',
                ['Company','Result evidence','Standalone','Consolidated','Stored/docs','Status'],
                [(r['current_legal_name'],r['result_evidence'],r['standalone'],r['consolidated'],f"{r['stored']}/{r['documents']}", 'Failed' if r['failed'] else 'Both scopes' if r['standalone'] and r['consolidated'] else 'Partial' if r['standalone'] or r['stored'] else 'Pending/unknown') for r in data['companies'][:5 if compact else 100]]))
        parts.append(grid('OFFICIAL RESULT DISCOVERY · selected quarter · incomplete coverage',['Source','Filings','Mapped companies','Unmapped filings','Last detection'],[(r['exchange'],r['filings'],r['mapped_companies'],r['unresolved_filings'],r['last_detected']) for r in data['filings']]))
        parts.append(Text('SQL refresh '+str(data['query_seconds'])+'s · '+data['period_semantics'],style='dim'))
    active = inspection.get('processes')
    parts.append(Text('GLOBAL WORKERS · '+(', '.join(r.get('container','?') for r in active) if active else 'none observed; check timestamp')+' · process observation '+str(inspection.get('processes_at')),style='cyan'))
    parts.append(Text('GLOBAL SCHEDULE · heartbeat '+str(feed['tick'].get('checked_at_utc'))+' · '+('PAUSED' if feed['paused'] else 'admissions enabled')+' · a scheduled/idle batch does not prove acquisition'))
    for name,value in feed.get('blockers',{}).items():
        parts.append(Text('GLOBAL BLOCKER '+name+': '+str(value.get('error') if isinstance(value,dict) else value),style='yellow'))
    return Panel(Group(*parts),title='TRADING AI — '+('DOCUMENT DOWNLOADS' if view in ('downloads','ratings') else 'ACQUISITION MONITOR'),subtitle='Ctrl+C closes view · display selection never changes acquisition policy',border_style='cyan')



def render(feed, inspection, view='all', compact=False):
    if view == 'details':
        return render_details(feed, inspection, view, compact)
    from acquisition_monitor_view import Monitor
    return Monitor(feed, inspection, compact)
