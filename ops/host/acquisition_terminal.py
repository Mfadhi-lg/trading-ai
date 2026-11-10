"""Host terminal UI for the bounded acquisition workflow. No web runtime."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import shlex
import sys
import threading
import time
from zoneinfo import ZoneInfo
import select
import contextlib
import termios
import tty

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'ops/host'), str(ROOT / 'ops/activation'), str(ROOT)]
import acquisition_lifecycle as lifecycle
import acquisition_observations as observations
from scrapper.view.monitor import _clean_path, _display_outcome, _format_duration
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

IST = ZoneInfo('Asia/Kolkata')
from reporting_quarters import default_quarter, normalize as normalize_quarter, label as reporting_label

SELECTED_QUARTER = None

STYLE = {'running': 'bold green', 'scheduled': 'cyan', 'completed': 'green',
         'accepted': 'green', 'failed': 'red', 'blocked': 'yellow', 'paused': 'yellow',
         'unknown': 'dim', 'idle': 'dim'}


def at(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(IST).strftime('%d %b %H:%M:%S IST')
    except (ValueError, TypeError, AttributeError):
        return 'not observed'


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def log_records(path):
    """Read bounded receipt tails, including an in-progress company's symbol."""
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            lines = stream.read().decode(errors='replace').splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                records.append(row)
        except ValueError:
            continue
    return records


class ReceiptFeed:
    def __init__(self):
        from candle_monitor import CandleFeed
        self.candles = CandleFeed()
        self.cache = {}

    def sample(self):
        """No Docker, database, network request, source request or write here."""
        state = lifecycle.dispatcher.STATE
        jobs = []
        paths = sorted(state.glob('*/admission.json'), key=lambda p: p.parent.name, reverse=True)
        for path in paths:
            outcome_path = path.with_name('outcome.json')
            try:
                fingerprint = (path.stat().st_mtime_ns, outcome_path.stat().st_mtime_ns if outcome_path.exists() else None)
            except OSError:
                continue
            saved = self.cache.get(path.parent.name)
            if saved and saved[0] == fingerprint:
                row = saved[1]
            else:
                admission = read_json(path)
                if not isinstance(admission, dict):
                    continue
                outcome = read_json(outcome_path) or {}
                started, finished = admission.get('started_at_utc'), outcome.get('finished_at_utc')
                duration = None
                try:
                    duration = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
                except (TypeError, ValueError):
                    pass
                row = dict(id=path.parent.name, job=admission.get('job', '?'),
                           exchange=admission.get('exchange', ''), started_at=started, updated_at=finished or started,
                           finished_at=finished, duration_seconds=duration,
                           state=({'PASS': 'completed', 'DEFERRED_ENVIRONMENT': 'blocked',
                                   'SKIPPED_ALREADY_ATTEMPTED': 'idle', 'IDLE':'idle', 'EXPIRED_AUTHORIZATION':'blocked', 'BLOCKED_SOURCE_POLICY':'blocked'}.get(outcome.get('status'), 'failed')) if outcome else 'unknown',
                           error=outcome.get('error') or outcome.get('reason'),
                           complete=bool(outcome))
                self.cache[path.parent.name] = (fingerprint, row)
            jobs.append(row)
        jobs.sort(key=lambda r: r.get('updated_at') or '', reverse=True)
        # Only recent admissions can supply current activity hints. An unfinished
        # historic receipt is never proof of a live worker.
        recent = jobs[:20]
        companies, document_receipt, document_receipts = [], None, {}
        for job in recent:
            records = log_records(state / job['id'] / 'runtime.log')
            current = None
            for record in records:
                if record.get('stage') == 'screener_request_started':
                    from urllib.parse import urlparse
                    path = urlparse(record.get('url', '')).path
                    if path.startswith('/company/'):
                        current = _clean_path(path)
                if record.get('stage') == 'financial_batch_outcome':
                    result = record.get('result', {})
                    claimed = {r['queue_item_id']: r for r in result.get('claimed', [])}
                    for outcome in result.get('outcomes', []):
                        item = claimed.get(outcome.get('queue_item_id'), {})
                        companies.append(dict(symbol=_clean_path(item.get('company_path_normalized')),
                            period=item.get('result_period', '?'), scope=item.get('reporting_scope', '?'),
                            status=outcome.get('status', '?'), updated_at=job.get('updated_at')))
                if record.get('stage') == 'document_stage_outcome':
                    if document_receipt is None:
                        document_receipt = record
                    document_receipts.setdefault(record.get('document_stage'), record)
            job = dict(job, current_symbol=current)
            jobs[jobs.index(next(r for r in jobs if r['id'] == job['id']))] = job
        tick = read_json(state / 'last-tick.json') or {}
        blockers = {p.name: read_json(p) for p in state.glob('*.stopped.json')}
        for name,value in blockers.items():
            if isinstance(value,dict) and not value.get('error'):
                stage=value.get('exchange')
                job=next((j for j in jobs if j.get('exchange')==stage and j.get('updated_at')==value.get('finished_at_utc')),None)
                if job:
                    failure=next((r for r in reversed(log_records(state/job['id']/'runtime.log')) if r.get('stage')=='execution_failure'),{})
                    blockers[name]={**value,'error':failure.get('error') or f"Stage stopped (exit {value.get('exit_code','unknown')}); /logs --job {job['id']}"}
                else:blockers[name]={**value,'error':f"Stage stopped (exit {value.get('exit_code','unknown')}); use /logs"}

        try:
            schedule = lifecycle.schedule_status()
        except Exception as exc:
            schedule = {'error': str(exc)}
        return observations.redact(dict(observed_at_utc=datetime.now(timezone.utc).isoformat(),
            paused=lifecycle.PAUSE.exists(), tick=tick, jobs=jobs, companies=companies,
            document_receipt=document_receipt, document_receipts=document_receipts,
            historical_candles=self.candles.sample(),
            blockers=blockers, schedule=schedule))


class Inspector:
    """Slow guarded checks run away from the terminal paint/receipt loop."""
    def __init__(self, every=120, quarter=None, term='', offset=0):
        self.quarter = quarter
        self.term, self.offset = term, offset
        self.quarter_data = self.quarter_at = self.quarter_error = None
        self.request_refresh = threading.Event()
        self.every = every
        self.mutex = threading.Lock()
        self.stop = threading.Event()
        self.raw = None
        self.database = None
        self.database_at = None
        self.database_source = None
        self.inspecting = False
        self.thread = None
        self.process_thread = None
        self.processes = None
        self.processes_at = None
        self.process_error = None
        candidates = []
        for name in ('wake-verification.json', 'shutdown-verification.json'):
            path = lifecycle.STATE / name
            value = read_json(path) or {}
            db = value.get('checks', {}).get('database')
            if value.get('status') == 'PASS' and db and db.get('database') == '/data/scraper.db':
                observed = value['checks'].get('ownership', {}).get('observed_at')
                candidates.append((observed or '', db, str(path.relative_to(ROOT))))
        if candidates:
            self.database_at, self.database, self.database_source = max(candidates, key=lambda r: r[0])

    def accept(self, raw):
        with self.mutex:
            self.raw = raw
            db = raw.get('live_inspection', raw.get('quiescent_inspection', {})).get('database')
            if db:
                self.database, self.database_at = db, raw['observed_at_utc']
                self.database_source = 'guarded read-only production census'

    def inspect(self):
        with self.mutex:
            self.inspecting = True
        try:
            if self.quarter is None:
                self.accept(lifecycle.status(include_summary=True, live=True))
            else:
                selected = self.quarter
                value = observations.backend.company('quarter_monitor', quarter=selected, term=self.term, offset=self.offset)
                with self.mutex:
                    if selected == self.quarter:
                        self.quarter_error = value.get('error')
                        if value.get('data') is not None:
                            self.quarter_data, self.quarter_at = value['data'], value['updated_at']
        except Exception as exc:
            self.accept({'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                         'inspection_errors': [str(exc)], 'status': 'DEGRADED'})
        finally:
            with self.mutex:
                self.inspecting = False

    def start(self):
        def loop():
            while not self.stop.is_set():
                self.inspect()
                self.request_refresh.wait(self.every)
                self.request_refresh.clear()
        self.thread = threading.Thread(target=loop, name='terminal-read-only-inspection', daemon=True)
        self.thread.start()
        def process_loop():
            while not self.stop.is_set():
                try:
                    rows = lifecycle.containers()
                    active = [dict(container=row['name'], **{k: row.get(k) for k in ('service', 'state', 'receipt_stage', 'started_at')})
                              for row in rows if row['state'] in lifecycle.ACTIVE and row['classification'] == 'current bounded adapter']
                    with self.mutex:
                        self.processes = active
                        self.processes_at = datetime.now(timezone.utc).isoformat()
                        self.process_error = None
                except Exception as exc:
                    with self.mutex:
                        self.processes = None
                        self.process_error = str(exc)
                self.stop.wait(10)
        self.process_thread = threading.Thread(target=process_loop, name='terminal-process-check', daemon=True)
        self.process_thread.start()

    def snapshot(self):
        with self.mutex:
            return dict(quarter=self.quarter, quarter_data=self.quarter_data, quarter_at=self.quarter_at, quarter_error=self.quarter_error, raw=self.raw, database=self.database, database_at=self.database_at,
                        database_source=self.database_source, inspecting=self.inspecting,
                        processes=self.processes, processes_at=self.processes_at, process_error=self.process_error)

    def close(self, console):
        self.stop.set()
        self.request_refresh.set()
        if self.thread and self.thread.is_alive():
            console.print('Finishing the current read-only inspection…', style='dim')
            # Never release host ownership while its immutable census still runs.
            self.thread.join()
        if self.process_thread:
            self.process_thread.join()


def format_document_queue(rows):
    if not isinstance(rows, list):
        return 'Document queue counts not available in this receipt.'
    labels = {'PENDING':'pending', 'SUCCEEDED':'saved', 'RETRYABLE_FAILURE':'retryable',
              'TERMINAL_FAILURE':'terminal', 'LEASED':'leased'}
    return 'Document queue: ' + ' · '.join(f"{labels[r['status']]} {r['n']:,}" for r in rows if r.get('status') in labels)


def table(title, columns):
    value = Table(title=title, title_justify='left', title_style='bold cyan', expand=True,
                  box=None, header_style='bold dim', padding=(0, 1))
    for column in columns:
        value.add_column(column)
    return value


def styled(state):
    return Text(str(state).replace('_', ' '), style=STYLE.get(state, 'white'))


def queue_count(db, name, states):
    rows = (db or {}).get('queues', {}).get(name)
    if rows is None:
        return '—'
    return f"{sum(r['n'] for r in rows if r.get('state', '').lower() in states):,}"


def quarter_label(period):
    try:
        year, month = map(int, period[:7].split('-'))
        quarter = {6: 1, 9: 2, 12: 3, 3: 4}[month]
        return f'{period[:7]} · Q{quarter} FY{year + (month != 3)}'
    except (ValueError, TypeError, KeyError):
        return str(period or 'unknown period')


def explain_error(value):
    text = str(value or 'No stored failure detail')
    try:
        detail = json.loads(text)
        if isinstance(detail, dict):
            text = str(detail.get('reason') or detail.get('error') or detail.get('message') or text)
    except (ValueError, TypeError):
        pass
    meanings = {'http_403': 'Publisher refused access (HTTP 403)',
                'http_503': 'Publisher temporarily unavailable (HTTP 503)',
                'http_500': 'Publisher server error (HTTP 500)',
                'html_response': 'Received a webpage instead of a document (html_response)',
                'no_validated_report_target': 'No verified report download link (no_validated_report_target)'}
    text = meanings.get(text, text)
    return text


def blocker_message(name, blocker):
    reason = explain_error(blocker.get('error', blocker) if isinstance(blocker, dict) else blocker)
    if 'EMPTY_OR_REJECTED_ROWS' in reason:
        return (f'BLOCKED {name}: BSE opened, but its results failed validation. '
                'Empty feed or unsupported rows; automatic retry is OFF. /bse shows the retained diagnosis.')
    return f'BLOCKED {name}: {reason}'


def coverage_periods(summary):
    periods = {}
    for row in summary.get('quarterly_coverage', []):
        periods.setdefault(row['period_end'], {})[row['reporting_scope']] = row['companies']
    return list(periods.items())


def coverage_table(summary, limit=6):
    output = table('QUARTERLY DATA · distinct companies with stored quarterly results',
                   ['Quarter / Indian FY', 'Standalone', 'Consolidated'])
    for period, scopes in coverage_periods(summary)[:limit]:
        output.add_row(quarter_label(period), *(f'{scopes[s]:,}' if s in scopes else '—'
                                                for s in ('standalone', 'consolidated')))
    if not summary.get('quarterly_coverage'):
        output.add_row('Awaiting guarded coverage census', '—', '—')
    return output


def render_monitor(feed, inspection, view='all', compact=False):
    if inspection.get('quarter') is not None:
        from acquisition_quarter_view import render
        return render(feed, inspection, view, compact)
    raw = inspection.get('raw') or {}
    db = inspection.get('database')
    summary = (db or {}).get('summary', {})
    checked = inspection.get('processes_at') or (raw.get('observed_at_utc') if 'active_bounded_stages' in raw else None)
    active = inspection.get('processes')
    if active is None:
        active = [] if inspection.get('process_error') else raw.get('active_bounded_stages', [])
    # Container observations have an explicit inspection timestamp. A new log
    # line is useful activity evidence but not a replacement for process checks.
    tick_at = feed['tick'].get('checked_at_utc')
    paused = feed['paused']
    try:
        tick_age = (datetime.now(timezone.utc) - datetime.fromisoformat(tick_at)).total_seconds()
    except (TypeError, ValueError):
        tick_age = float('inf')
    state = 'paused' if paused else 'running' if active else 'scheduled' if tick_age < 180 else 'unknown'
    activity = ('PAUSED · an existing batch may still be draining' if paused and active else
                'PAUSED · new batches will not start' if paused else
                'RUNNING · a batch process is present' if active else
                'WAITING · scheduler heartbeat is recent; no batch observed' if tick_age < 180 and checked and not inspection.get('process_error') else
                'CHECKING · scheduler is alive; batch process check unavailable' if tick_age < 180 else
                'UNKNOWN · scheduler heartbeat is stale or missing')
    header = Text.assemble(('TRADING AI  ', 'bold cyan'), ('LIVE SCRAPER MONITOR', 'bold white'),
                           ('   ' + activity, STYLE[state]), ('\n' + at(feed['observed_at_utc']), 'dim'))
    parts = [header]
    next_job = feed['schedule'].get('next_scheduled_batch') or {}
    parts.append(Text(f"Heartbeat: {at(tick_at)}   |   Next: {next_job.get('job', 'unknown')} @ {at(next_job.get('next_at_ist'))}", style='cyan'))
    counts_title = f"QUEUE TOTALS · census {at(inspection.get('database_at'))}"
    counts = table(counts_title, ['Queue', 'Pending', 'Claimed / leased', 'Accepted / saved', 'Skipped', 'Retryable', 'Terminal'])
    counts.add_row('Financial companies', queue_count(db, 'screener_company_queue', {'pending'}),
        queue_count(db, 'screener_company_queue', {'claimed', 'running'}), queue_count(db, 'screener_company_queue', {'accepted'}),
        queue_count(db, 'screener_company_queue', {'skipped_already_accepted'}), queue_count(db, 'screener_company_queue', {'failed_retryable'}),
        queue_count(db, 'screener_company_queue', {'failed_terminal', 'manual_review', 'failed'}))
    for label, name in [('Documents', 'artifact_download_queue'), ('Credit ratings', 'credit_rating_download_queue')]:
        counts.add_row(label, queue_count(db, name, {'pending'}), queue_count(db, name, {'leased', 'running'}),
            queue_count(db, name, {'succeeded'}), queue_count(db, name, {'skipped_already_have_content', 'skipped_policy'}),
            queue_count(db, name, {'retryable_failure'}), queue_count(db, name, {'terminal_failure', 'interaction_required'}))
    parts += [Text(''), counts]
    companies = summary.get('companies')
    accepted = summary.get('accepted_pages')
    extraction = {r['state']: r['n'] for r in summary.get('document_contents', [])}
    label = lambda value: f'{value:,}' if isinstance(value, int) else '—'
    parts.append(Text(f"Companies: {label(companies)}  |  Accepted financial pages: {label(accepted)}  |  Extracted OK: {label(extraction.get('ok'))}  |  Processing pending: {label(extraction.get('not_started'))}", style='dim'))
    exclusions = {}
    for row in summary.get('financial_outcomes', []):
        outcome = _display_outcome(row['status'], row.get('last_error_json'))
        if outcome.startswith('skipped_'):
            exclusions[outcome] = exclusions.get(outcome, 0) + row['n']
    if exclusions:
        parts.append(Text('Stored terminal rows include policy skips: ' + ', '.join(f"{k.replace('skipped_', '').replace('_', ' ')} {v:,}" for k, v in exclusions.items()), style='dim'))
    freshness = f"Counts are a saved observation; backlog includes historical/ineligible work. Source: {inspection.get('database_source') or 'no census yet'}."
    parts.append(Text(freshness, style='dim'))
    if inspection.get('inspecting'):
        parts.append(Text('Refreshing guarded checks in the background; receipt feed remains live.', style='dim'))
    parts += [coverage_table(summary)]
    parts.append(Text('Coverage counts stored companies per scope, not all listed companies or certified filings.', style='dim'))
    scope = summary.get('collection_scope', [])
    if scope:
        parts.append(Text('Collection universe: ' + ' · '.join(f"{r['state'].replace('_',' ')} {r['n']:,}" for r in scope), style='cyan'))
    parts.append(Text('Pending = queued work, not running. Retryable = a past failure, not an active retry. '
                      'Batches have limits; historical work and excluded companies remain in totals.', style='dim'))
    diagnostics = table('WHY WORK IS WAITING · saved queue state, not admission eligibility',
                        ['Queue', 'Never attempted', 'Retry time due*', 'Retry later', 'Largest stored retry reason'])
    for row in summary.get('queue_diagnostics', []):
        reason = next(iter(row.get('reasons', [])), {})
        diagnostics.add_row(row['queue'].replace('_download_queue','').replace('screener_company_queue','Financial'),
            str(row.get('unattempted') or 0), str(row.get('retry_due') or 0), str(row.get('retry_waiting') or 0),
            f"{reason.get('n', 0):,} · {explain_error(reason.get('reason'))}" if reason else 'No retryable rows')
    parts += [diagnostics, Text('*Due time alone does not allow a claim: scope, policy, attempt limits and schedule still apply.', style='dim')]
    quarters = table('FINANCIAL WORK BY QUARTER · queue rows, including history', ['Quarter / Indian FY', 'Scope', 'State', 'Rows'])
    for row in summary.get('quarterly_queue', [])[:16]:
        quarters.add_row(quarter_label(row['result_period']), row['reporting_scope'], row['status'], f"{row['n']:,}")
    parts.append(quarters)
    worker = table(f"ACTIVE BATCHES · process observation {at(checked)}", ['Stage / container', 'State', 'Company / request', 'Started'])
    for item in active:
        related = next((j for j in feed['jobs'] if not j['complete'] and (j.get('exchange', '').lower() in item.get('container', '').lower() or ('financial' in j.get('job', '') and 'financial' in item.get('container', '')))), {})
        worker.add_row(item.get('container', '?'), styled(item.get('state', 'unknown')),
                       str(related.get('current_symbol') or 'See runtime log'), at(item.get('started_at')))
    if not active:
        worker.add_row('No active adapter in the last process check' if checked else 'Process inspection pending', '', '', '')
    hints = [j for j in feed['jobs'][:10] if not j['complete'] and j.get('current_symbol')]
    for job in hints[:2]:
        worker.add_row(job['job'] + ' (receipt only)', styled('unknown'), job['current_symbol'], at(job['started_at']))
    parts += [Text(''), worker]
    company_rows = feed['companies'][:6]
    recent = table('RECENT COMPANY RESULTS · receipt-backed', ['Company', 'Period', 'Scope', 'Result', 'Finished'])
    for item in company_rows[:4 if compact else 6]:
        recent.add_row(item['symbol'], item['period'], item['scope'], styled(item['status']), at(item['updated_at']))
    if not company_rows:
        for item in summary.get('recent_financial_jobs', [])[:6]:
            result = _display_outcome(item['status'], item.get('last_error_json'))
            recent.add_row(_clean_path(item.get('company_path_normalized')), item.get('result_period', '?'),
                           item.get('reporting_scope', '?'), styled(result), at(item.get('updated_at_utc')))
    parts += [Text(''), recent]
    history = table('RECENT SCHEDULED BATCHES', ['Finished / admitted', 'Stage', 'Result', 'Duration', 'Reason'])
    for job in feed['jobs'][:4 if compact else 6]:
        history.add_row(at(job['updated_at']), job['job'], styled(job['state']),
                        _format_duration(job['duration_seconds']), str(job.get('error') or ''))
    parts += [Text(''), history]
    schedule = table('NEXT SLOTS · bounded batches, no missed-slot replay', ['Stage', 'Next · IST', 'Limit'])
    financial = feed['schedule'].get('financial', {})
    limits = {'FINANCIAL': f"{financial.get('financial_max_companies', 2)} companies",
              'CAP': f"{financial.get('cap_max_checks', 40)} checks", 'DOCUMENT_DOWNLOAD': '1 download',
              'DOCUMENT_PROCESSING': '1 document'}
    for job in feed['schedule'].get('jobs', [])[:6]:
        schedule.add_row(job['job'], at(job.get('next_at_ist')), limits.get(job.get('exchange'), 'bounded slot'))
    parts += [Text(''), schedule]
    operations_start = len(parts)
    store = raw.get('storage') or {}
    free = store.get('free_bytes')
    parts.append(Text(f"My Passport: {f'{free / 1e9:.1f} GB free (verified {at(checked)})' if free is not None else 'verification pending / unavailable'}", style='cyan'))
    document = feed.get('document_receipt')
    if document:
        parts.append(Text(f"Latest document stage: {document.get('document_stage')} @ {at(document.get('finished_at_utc'))}\n{format_document_queue(document.get('queue_depth', {}))}", style='dim'))
    expiry = feed['schedule'].get('document_expires_at_utc')
    parts.append(Text(f"Document window: {'EXPIRED' if feed['schedule'].get('document_window_expired') else 'ends'} {at(expiry)}", style='yellow' if feed['schedule'].get('document_window_expired') else 'dim'))
    for name, blocker in feed['blockers'].items():
        parts.append(Text(blocker_message(name, blocker), style='yellow'))
    for error in summary.get('detail_errors', []):
        parts.append(Text('Coverage/queue detail unavailable: ' + error, style='yellow'))
    if db and db.get('summary_error'):
        parts.append(Text('Database summary unavailable: ' + db['summary_error'], style='yellow'))
    for error in raw.get('inspection_errors', []):
        parts.append(Text('Inspection: ' + str(error), style='yellow'))
    if inspection.get('process_error'):
        parts.append(Text('Process inspection unavailable: ' + inspection['process_error'], style='yellow'))
    if feed['schedule'].get('error'):
        parts.append(Text('Schedule unavailable: ' + feed['schedule']['error'], style='red'))
    if view in ('downloads', 'ratings'):
        names = [('Credit ratings', 'credit_rating_download_queue')] if view == 'ratings' else [('Documents', 'artifact_download_queue'), ('Credit ratings', 'credit_rating_download_queue')]
        details = table('CREDIT-RATING QUEUE' if view == 'ratings' else 'DOCUMENT DOWNLOAD QUEUES', ['Queue', 'State', 'Rows'])
        for label_, name in names:
            for row in (db or {}).get('queues', {}).get(name, []):
                details.add_row(label_, row.get('state', '?'), f"{row['n']:,}")
        processing = table('PROCESSING · saved content states', ['State', 'Contents'])
        for row in summary.get('document_contents', []):
            processing.add_row(row['state'], f"{row['n']:,}")
        batches = table('RECENT DOCUMENT BATCHES', ['Finished', 'Stage', 'Outcome', 'Duration'])
        for job in [j for j in feed['jobs'] if j.get('exchange', '').startswith('DOCUMENT_')][:6]:
            batches.add_row(at(job['updated_at']), job['job'], styled(job['state']), _format_duration(job['duration_seconds']))
        parts = [header, parts[1], Text(''), details, Text('Census: ' + at(inspection.get('database_at')), style='dim'),
                 Text(''), processing, Text(''), batches, Text(''), *parts[operations_start:]]
        compact = False
    if compact:
        if Console().size.height < 45:
            # Keep drive, pause and blockers visible in a normal 80x24 terminal.
            # The scrollable --once/status output retains the full tables.
            short = table('', ['Queue', 'Pending', 'Claimed*', 'Saved', 'Skipped', 'Retry', 'Terminal'])
            for label_, name, done, skip, retry_, failed in [
                ('Financial', 'screener_company_queue', {'accepted'}, {'skipped_already_accepted'}, {'failed_retryable'}, {'failed_terminal','failed','manual_review'}),
                ('Documents', 'artifact_download_queue', {'succeeded'}, {'skipped_already_have_content','skipped_policy'}, {'retryable_failure'}, {'terminal_failure'}),
                ('Ratings', 'credit_rating_download_queue', {'succeeded'}, {'skipped_policy'}, {'retryable_failure'}, {'terminal_failure','interaction_required'}),
            ]:
                short.add_row(label_, queue_count(db,name,{'pending'}), queue_count(db,name,{'claimed','running','leased'}),
                              queue_count(db,name,done),queue_count(db,name,skip),queue_count(db,name,retry_),queue_count(db,name,failed))
            line = lambda text, style='': Text(text, style=style, no_wrap=True, overflow='ellipsis')
            mini = [line(f"TRADING AI · {activity}", STYLE[state]),
                line(f"Next slot: {next_job.get('job','unknown')} @ {at(next_job.get('next_at_ist'))}" + (' (paused)' if paused else ''), 'cyan'),
                line('Queue census: ' + at(inspection.get('database_at')) + ' · historical backlog included', 'dim'),
                short,
                line(f"Companies {label(companies)} · Financial pages {label(accepted)} · Extracted {label(extraction.get('ok'))}", 'dim'),
                line('*Claimed = saved queue state; live batch processes are checked separately.', 'dim'),
                line('Adapters: ' + (', '.join(r.get('container','?') for r in active) if active else 'none observed' if checked and not inspection.get('process_error') else 'inspection pending / unavailable')),
                line('Process check: ' + at(checked), 'dim')]
            if company_rows:
                for item in company_rows[:1]:
                    mini.append(line(f"{item['symbol']} · {item['period']} · {item['status']} · {at(item['updated_at'])}", 'green'))
            for period, scopes in coverage_periods(summary)[:2]:
                standalone = f"{scopes['standalone']:,}" if 'standalone' in scopes else '—'
                consolidated = f"{scopes['consolidated']:,}" if 'consolidated' in scopes else '—'
                mini.append(line(f"{quarter_label(period)} · companies: Standalone {standalone} · Consolidated {consolidated}", 'cyan'))
            mini.append(line('Pending = waiting work · Retry = past failures · /coverage: quarterly detail', 'dim'))
            mini += [line(f"My Passport: {f'{free / 1e9:.1f} GB free' if free is not None else 'unavailable / not checked'}", 'cyan'),
                     line(f"Documents: {'EXPIRED' if feed['schedule'].get('document_window_expired') else 'window ends'} {at(expiry)}", 'dim')]
            for name, blocker in list(feed['blockers'].items())[:2]:
                message = blocker_message(name, blocker)
                if 'EMPTY_OR_REJECTED_ROWS' in str(blocker):
                    message = f'BLOCKED {name}: validation failed · /bse explains · retries OFF'
                mini.append(line(message, 'yellow'))
            errors = raw.get('inspection_errors', [])
            if errors:
                mini.append(line('Inspection: ' + str(errors[0]), 'yellow'))
            if inspection.get('inspecting'):
                mini.append(line('Guarded checks refreshing in background…', 'dim'))
            mini.append(line('/coverage: full quarterly view · /company: financials · Ctrl+C: exit', 'dim'))
            return Panel(Group(*mini), border_style='cyan', padding=(0,1))
        columns = Table.grid(expand=True, padding=(0, 2))
        if Console().width >= 110:
            columns.add_column(ratio=1)
            columns.add_column(ratio=1)
            columns.add_row(recent, history)
        else:
            columns.add_column()
            columns.add_row(recent)
        parts = [header, parts[1], Text(activity, style=STYLE[state]), counts, parts[4], coverage_table(summary, 4), Text(''), worker,
                 Text(''), columns, Text(''), *parts[operations_start:]]
        parts.append(Text('./scraper status prints full history/schedules · /company TICKER shows financials', style='dim'))
    return Panel(Group(*parts), title='[bold green]LIVE SCRAPER PROCESS MONITOR[/bold green]',
        subtitle='Ctrl+C closes this view · schedule unchanged · counts keep their census timestamp',
        border_style='cyan', padding=(1, 2))


@contextlib.contextmanager
def keyboard_mode(cooked=False):
    if not sys.stdin.isatty():
        yield
        return
    old = termios.tcgetattr(sys.stdin)
    try:
        if cooked:
            attrs = termios.tcgetattr(sys.stdin)
            attrs[3] |= termios.ICANON | termios.ECHO
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, attrs)
        else:
            tty.setcbreak(sys.stdin.fileno())
        yield
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def read_key(seconds):
    if sys.stdin.isatty():
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
        return sys.stdin.read(1) if ready else ''
    time.sleep(seconds)
    return ''


def pick_quarter(choices=None):
    if choices is None:
        value = observations.backend.company('quarter_options')
        choices = [r['value'] for r in (value.get('data') or {}).get('options', [])]
    choices = list(dict.fromkeys(['all', *(choices or []), 'unknown']))
    console = Console(markup=False)
    for i, value in enumerate(choices, 1):
        console.print(f'{i}. {reporting_label(value)}')
    value = input('Reporting quarter (number or YYYY-MM): ').strip()
    return choices[int(value)-1] if value.isdigit() and 1 <= int(value) <= len(choices) else normalize_quarter(value)


def run_monitor(refresh=5, inspect_every=120, once=False, as_json=False, view='all', quarter=None, term='', offset=0):
    global SELECTED_QUARTER
    console = Console(markup=False, highlight=False)
    feed, inspector = ReceiptFeed(), Inspector(inspect_every, quarter=quarter, term=term, offset=offset)
    if once:
        # Fast snapshot never waits for Docker or a database census.
        if quarter is not None:
            inspector.inspect()
        current = feed.sample()
        snapshot = inspector.snapshot()
        if as_json:
            print(json.dumps({'feed': current, 'inspection': snapshot}, indent=2))
        else:
            console.print(render_monitor(current, snapshot, view))
        return 1 if quarter is not None and not snapshot.get('quarter_data') else 0
    inspector.start()
    try:
        with keyboard_mode(), Live(render_monitor(feed.sample(), inspector.snapshot(), view, compact=True), console=console,
                  auto_refresh=False, screen=True, vertical_overflow='ellipsis') as live:
            while True:
                key = read_key(refresh)
                if key in ('[', ']', 'a', 'u', 'q', 'c') and inspector.quarter is not None:
                    choices = [r['value'] for r in (inspector.quarter_data or {}).get('options', [])]
                    if key == 'q':
                        live.stop()
                        with keyboard_mode(cooked=True):
                            selected = pick_quarter(choices)
                        live.start()
                    elif key == 'c':
                        selected = default_quarter(datetime.now(IST).date())
                    elif key in ('a','u'):
                        selected = 'all' if key == 'a' else 'unknown'
                    else:
                        periods = sorted([v for v in choices if v not in ('all','unknown')], reverse=True)
                        index = periods.index(inspector.quarter) if inspector.quarter in periods else 0
                        selected = periods[max(0,min(len(periods)-1,index+(1 if key=='[' else -1)))] if periods else inspector.quarter
                    with inspector.mutex:
                        inspector.quarter = selected
                        SELECTED_QUARTER = selected
                        inspector.quarter_data = inspector.quarter_at = None
                    inspector.request_refresh.set()
                elif key == 'r':
                    inspector.request_refresh.set()
                live.update(render_monitor(feed.sample(), inspector.snapshot(), view, compact=True), refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        inspector.close(console)
    console.print('Monitor closed. Acquisition schedule unchanged.', style='dim')
    return 0


def print_rows(console, rows, fields, title):
    output = table(title, [label for key, label in fields])
    for row in rows:
        output.add_row(*(Text(str(row.get(key) if row.get(key) is not None else '—')) for key, label in fields))
    if not rows:
        output.add_row('No records', *('' for _ in fields[1:]))
    console.print(output)


def show_search(term, as_json=False):
    result = observations.backend.company('search', term=term)
    if as_json:
        print(json.dumps(result, indent=2))
    elif result.get('error'):
        Console(markup=False).print('Company query deferred: ' + result['error'] + '\nTry again after the active batch.')
    else:
        print_rows(Console(markup=False), (result.get('data') or {}).get('companies', [])[:40],
                   [('company_id', 'ID'), ('symbol', 'Ticker'), ('current_legal_name', 'Company'), ('status', 'State')], 'COMPANY SEARCH')
    return 1 if result.get('error') else 0


def show_company(term, as_json=False, quarter=None):
    details = observations.backend.company('company_report', term=term, quarter=quarter or 'all')
    console = Console(markup=False, highlight=False)
    if as_json:
        print(json.dumps(details, indent=2))
        return 1 if details.get('error') else 0
    if details.get('error'):
        console.print('Company query deferred: ' + details['error'] + '\nTry again after the active batch.')
        return 1
    value = details.get('data') or {}
    company = value.get('company')
    if not company:
        matches = value.get('matches') or []
        if matches:
            console.print('Multiple companies match. Use a ticker or database ID:')
            print_rows(console, matches[:40], [('company_id','ID'),('symbol','Ticker'),('current_legal_name','Company')], 'MATCHES')
        else:
            console.print('No company found: ' + term)
        return 1
    if quarter is not None:
        scoped = dict(data=value.get('quarter_acquisition'),updated_at=details.get('updated_at'),error=details.get('error'))
        console.print(render_monitor(ReceiptFeed().sample(),dict(quarter=quarter,quarter_data=scoped.get('data'),quarter_at=scoped.get('updated_at'),quarter_error=scoped.get('error'))))
    console.print('\nLIFETIME COMPANY REFERENCE · ' + company['current_legal_name'], style='bold cyan')
    print_rows(console, value.get('periods', []), [('period_end','Period ending'),
        ('reporting_scope','Scope'),('section_code','Statement'),('period_type','Frequency')], 'STORED FINANCIAL COVERAGE')
    print_rows(console, value.get('identifiers', []), [('identifier_type', 'Exchange / type'), ('identifier_value', 'Identifier')], 'IDENTIFIERS')
    console.print(value.get('financials') or 'Financial matrix unavailable: ' + str(value.get('financials_error', 'No accepted financials')))
    print_rows(console, value.get('financial_jobs', [])[:10], [('result_period', 'Period'), ('reporting_scope', 'Scope'), ('status', 'State'), ('updated_at_utc', 'Updated UTC')], 'FINANCIAL QUEUE')
    print_rows(console, value.get('documents', [])[:20], [('artifact_type', 'Type'), ('title_raw', 'Document'), ('period_end', 'Period'), ('acquisition_status', 'Download'), ('extraction_status', 'Extraction')], 'DOCUMENTS · up to 20 of the first 100 records')
    console.print('Observed ' + at(details.get('updated_at')) + '. /company TICKER --json shows all returned fields.', style='dim')
    return 0


def show_bse(as_json=False):
    """Replay retained evidence offline. No browser, source request or hold change."""
    from collections import Counter
    from scrapper.sources.exchange.result_filings import parse_result_filings, BSE_FINANCIAL_RESULTS
    result = {'blocker': read_json(lifecycle.dispatcher.STATE / 'BSE.stopped.json'),
              'automatic_retry': False, 'source': 'retained local response; offline replay'}
    paths = sorted(lifecycle.dispatcher.RECEIPTS.glob('*.json'), reverse=True)
    for path in paths:
        receipt = read_json(path) or {}
        raw = path.with_suffix('.raw')
        if not raw.is_file():
            continue
        result.update(receipt=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), status=receipt.get('status'),
                      http_status=receipt.get('response', {}).get('status_code'))
        try:
            parsed = parse_result_filings(raw.read_bytes(), BSE_FINANCIAL_RESULTS,
                                          result_period=receipt.get('result_period'))
            result.update(valid_filings=len(parsed.filings), rejected_rows=len(parsed.rejected),
                          excluded_representations=parsed.excluded_row_count,
                          reasons=dict(Counter(r.reason_detail for r in parsed.rejected)))
        except (ValueError, OSError) as exc:
            result['error'] = str(exc)
        break
    if as_json:
        print(json.dumps(observations.redact(result), indent=2))
    else:
        console = Console(markup=False)
        console.print('BSE · retained browser diagnosis', style='bold cyan')
        if result.get('blocker'):
            console.print(blocker_message('BSE.stopped.json', result['blocker']))
        else:
            console.print('No retained BSE stop marker.')
        if 'http_status' in result:
            console.print(f"HTTP {result['http_status']} · {result.get('valid_filings', 'unknown')} valid filing identities · {result.get('rejected_rows', 'unknown')} rejected source rows")
            for reason, count in result.get('reasons', {}).items():
                console.print(f'{count} rows: {reason}')
            console.print('Evidence: ' + result['receipt'])
            console.print('Any rejected row currently stops the whole batch. Unsupported period codes need an explicit parser decision before resuming BSE.')
        else:
            console.print('No retained raw response available to replay.')
        if result.get('error'):
            console.print(result['error'], style='yellow')
        console.print('Offline diagnosis only. No browser request or automatic retry was made.', style='dim')
    return 0


COMMANDS = ['/monitor', '/status', '/coverage', '/search',
            '/company', '/quarter', '/bse', '/doctor', '/logs', '/wake', '/shutdown', '/clear', '/help', '/exit']


def resolve_shell_command(value):
    command = value.lstrip('/').lower()
    command = {'scraper-monitor':'monitor','scraper-monitor.sh':'monitor'}.get(command,command)
    names = [name[1:] for name in COMMANDS]
    if command in names or command in ('quit', '?'):
        return command
    matches = [name for name in names if command and name.startswith(command)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError('Keep typing: ' + ', '.join('/' + name for name in matches))
    raise ValueError(f'Unknown command: /{command}. Type /help to see commands.')


class CompanySuggestions:
    """An in-memory catalog refreshed only through the existing guarded query."""
    def __init__(self):
        self.rows = None
        self.last_attempt = float('-inf')
        self.error = None

    def matches(self, term):
        if self.rows is None and time.monotonic() - self.last_attempt >= 60:
            self.last_attempt = time.monotonic()
            result = observations.backend.company('company_catalog')
            self.error = result.get('error')
            if not self.error:
                self.rows = (result.get('data') or {}).get('companies', [])
        normalized = lambda text: re.sub(r'[^a-z0-9]', '', str(text or '').lower())
        needle = normalized(term)
        rows = [r for r in self.rows or [] if not needle or any(needle in normalized(r.get(k))
                for k in ('company_id', 'symbol', 'current_legal_name', 'identifiers'))]
        rows.sort(key=lambda r: (0 if normalized(r.get('symbol')) == needle or str(r['company_id']) == term else 1,
                                 r['current_legal_name']))
        return rows[:12]


def shell_completer(catalog):
    from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
    class CompleterWithCompanies(Completer):
        def get_completions(self, document, complete_event):
            before = document.text_before_cursor
            match = re.match(r'^/?(\S+)\s+(.*)$', before)
            try:
                command = resolve_shell_command(match[1]) if match else None
            except ValueError:
                return
            if command in ('company', 'search'):
                term = match[2]
                for row in catalog.matches(term):
                    # Database ID is unique even when display names/tickers are not.
                    yield Completion(str(row['company_id']), start_position=-len(term),
                        display=f"{row.get('symbol') or row['company_id']}  {row['current_legal_name']}",
                        display_meta=f"ID {row['company_id']}")
            elif not re.search(r'\s', before):
                for command in COMMANDS:
                    if command.startswith(before):
                        yield Completion(command, start_position=-len(before))
    return ThreadedCompleter(CompleterWithCompanies())


HELP = '''TRADING AI · interactive terminal
Short prefixes work: /comp = /company, /mon = /monitor, /stat = /status
/monitor              Financial scraping, downloads, ratings and processing
/quarter 2026-09      Select September 2026 (YYYY-MM, quarter-end month)
/monitor --quarter 2026-09   All acquisition for September 2026
/scraper-monitor     Alias for /monitor
/status               Fast terminal snapshot
/coverage             Quarterly data, collection universe and queue reasons
/search CARERATING    Search name, ticker, BSE code, ISIN or company ID
/company CARERATING   Financial statements, periods, queue and documents
/bse                  Explain BSE failure using retained browser evidence
/doctor               Full guarded inspection (can take up to a minute)
/logs                 Recent dispatcher log; /logs --job JOB_ID
/wake                 Resume the existing guarded schedule
/shutdown             Drain acquisition before Finder drive ejection
/clear                Clear terminal
/help                 Show commands
/exit                 Exit shell; scraping continues'''


def shell():
    global SELECTED_QUARTER
    console = Console(markup=False, highlight=False)
    console.print(Panel(Text(HELP), title='TRADING AI CLI', border_style='cyan'))
    try:
        from prompt_toolkit import PromptSession
        catalog = CompanySuggestions()
        def toolbar():
            if catalog.error:
                return 'Company suggestions deferred: database is busy or unavailable. Keep typing a ticker, or retry in a minute.'
            if catalog.rows is None and catalog.last_attempt != float('-inf'):
                return 'Loading company suggestions through a guarded read…'
            return 'Type /company NAME · Tab selects a suggestion · /coverage quarterly data · /bse explains BSE'
        session = PromptSession(completer=shell_completer(catalog), complete_while_typing=True,
                                bottom_toolbar=toolbar) if sys.stdin.isatty() else None
    except ImportError:
        session = None
    while True:
        try:
            line = (session.prompt('trading-ai › ') if session else input('trading-ai > ')).strip()
        except (EOFError, KeyboardInterrupt):
            console.print('\nCLI closed. Acquisition schedule unchanged.')
            return 0
        if not line:
            continue
        try:
            args = shlex.split(line)
            command = resolve_shell_command(args[0])
            if command in ('company', 'search') and len(args) == 1:
                if session:
                    console.print('Type a name or ticker. Use arrows and Tab to select a result; Enter opens it. Suggestions may wait for an idle database window.', style='dim')
                    line = session.prompt('trading-ai › ', default='/' + command + ' ')
                    args = shlex.split(line)
                    if len(args) < 2:
                        continue
                    command = resolve_shell_command(args[0])
                else:
                    console.print('Type /' + command + ' NAME or TICKER, for example /' + command + ' TVSMOTOR.')
                    continue
            if command in ('exit', 'quit'):
                return 0
            if command in ('help', '?'):
                console.print(HELP)
            elif command == 'quarter':
                SELECTED_QUARTER = normalize_quarter(args[1]) if len(args)>1 else pick_quarter()
                console.print('Reporting Quarter: '+reporting_label(SELECTED_QUARTER))
            elif command == 'clear':
                console.clear()
            elif command in ('wake', 'shutdown'):
                # An explicit typed command uses the existing lifecycle runner.
                import subprocess
                subprocess.run([str(ROOT/'scraper'), command, *args[1:]], check=False)
            else:
                main([command, *args[1:]])
        except KeyboardInterrupt:
            console.print('\nBack to CLI.')
        except SystemExit:
            pass
        except (ValueError, RuntimeError) as exc:
            console.print(str(exc), style='yellow')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['monitor', 'status', 'coverage', 'bse', 'doctor', 'logs', 'shell', 'search', 'company'])
    parser.add_argument('term', nargs='*')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--quarter', type=normalize_quarter, help='Reporting period YYYY-MM or unknown')
    group.add_argument('--all-quarters', action='store_true')
    parser.add_argument('--offset', type=int, default=0, help='Quarter company/document page offset')
    parser.add_argument('--term', dest='company_filter', default='', help='Quarter company name or ID filter')
    parser.add_argument('--once', action='store_true', help='Print a fast snapshot and exit')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--refresh', type=float, default=5, help='Receipt/terminal refresh seconds (minimum 1)')
    parser.add_argument('--inspect-every', type=float, default=120, help='Background guarded inspection interval (minimum 60s)')
    parser.add_argument('--job')
    parser.add_argument('--lines', type=int, default=100)
    args = parser.parse_args(argv)
    quarter = 'all' if args.all_quarters else args.quarter or SELECTED_QUARTER or default_quarter(datetime.now(IST).date())
    if args.refresh < 1 or args.inspect_every < 60:
        parser.error('--refresh must be >=1 and --inspect-every must be >=60')
    if args.action == 'shell':
        return shell()
    if args.action == 'bse':
        return show_bse(args.json)
    if args.action == 'coverage':
        inspector = Inspector(quarter=quarter, term=args.company_filter, offset=args.offset)
        inspector.inspect()
        snapshot = inspector.snapshot()
        if args.json:
            print(json.dumps(observations.redact(snapshot), indent=2))
        else:
            Console(markup=False).print(render_monitor(ReceiptFeed().sample(), snapshot, view='details'))
        return 0 if snapshot.get('quarter_data') else 1
    if args.action == 'monitor':
        return run_monitor(args.refresh, args.inspect_every, args.once or args.json or not sys.stdout.isatty(), args.json, 'monitor', quarter=quarter, term=args.company_filter, offset=args.offset)
    if args.action == 'status' and not args.json:
        return run_monitor(once=True, quarter=quarter)
    if args.action in ('doctor', 'status'):
        result = lifecycle.status(include_summary=True, live=True)
        if args.json:
            print(json.dumps(observations.redact(result), indent=2))
        else:
            inspector = Inspector()
            inspector.accept(result)
            Console(markup=False, highlight=False).print(render_monitor(ReceiptFeed().sample(), inspector.snapshot()))
        return 0 if result.get('status') == 'PASS' else 1
    if args.action == 'logs':
        result = observations.logs(args.job, args.lines)
        print(json.dumps(result, indent=2) if args.json else str(result['data']))
        return 0
    if args.action in ('search', 'company'):
        term = ' '.join(args.term).strip()
        if not term:
            Console(markup=False).print('Type a company name, ticker or ID, e.g. ./scraper ' + args.action + ' TVSMOTOR. In ./scraper shell, /company offers suggestions as you type.')
            return 1
        return show_search(term, args.json) if args.action == 'search' else show_company(term, args.json, quarter=quarter)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
