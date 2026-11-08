"""Unified terminal dashboard using timestamped observations, never queue activity guesses."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from reporting_quarters import default_quarter, label
from scrapper.view.monitor import _clean_path, _display_outcome


def when(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %b %H:%M IST')
    except (ValueError, TypeError, AttributeError):
        return 'not observed'


def line(value, style=''):
    return Text(str(value), style=style, no_wrap=True, overflow='ellipsis')


def state(value):
    value = str(value or 'unknown').lower()
    names = {'succeeded': 'Downloaded', 'pending': 'Queued', 'leased': 'Leased*',
             'running': 'Running*', 'claimed': 'Claimed*', 'retryable_failure': 'Retry waiting',
             'failed_retryable': 'Retry waiting', 'failed_terminal': 'Failed',
             'terminal_failure': 'Failed', 'interaction_required': 'Needs interaction',
             'skipped_already_have_content': 'Already stored', 'skipped_policy': 'Policy skip'}
    style = ('green' if value in ('accepted', 'succeeded', 'ok', 'completed') else
             'yellow' if 'retry' in value or 'interaction' in value else
             'red' if 'fail' in value else 'cyan' if value in ('running', 'claimed', 'leased') else 'dim')
    return line(names.get(value, value.replace('_', ' ').capitalize()), style)


def table(columns):
    result = Table(box=None, expand=True, padding=(0, 1), pad_edge=False, header_style='dim')
    for column in columns:
        result.add_column(column, no_wrap=True, overflow='ellipsis')
    return result


def financial_rows(feed, data, quarter):
    """Receipt outcomes remain available between database observations."""
    records = []
    for row in feed.get('companies', []):
        if quarter != 'all' and row.get('period') != quarter:
            continue
        records.append((row.get('symbol'), row.get('period'), row.get('scope'),
                        row.get('status'), row.get('updated_at'), 'receipt'))
    for row in data.get('financial_jobs', []):
        symbol = _clean_path(row.get('company_path_normalized'))
        records.append((symbol, row.get('result_period'), row.get('reporting_scope'),
                        _display_outcome(row.get('status'), row.get('last_error_json')),
                        row.get('updated_at_utc'), 'census'))
    latest = {}
    for record in sorted(records, key=lambda r: r[4] or '', reverse=True):
        latest.setdefault(record[:3], record)
    return list(latest.values())


class Monitor:
    def __init__(self, feed, inspection, compact):
        self.feed, self.inspection, self.compact = feed, inspection, compact

    def __rich_console__(self, console, options):
        feed, inspection = self.feed, self.inspection
        q = inspection.get('quarter') or 'all'
        data = inspection.get('quarter_data') or {}
        short = self.compact and options.max_height < (36 if options.max_width >= 120 else 46)
        active = inspection.get('processes')
        checked = inspection.get('processes_at')
        process_error = inspection.get('process_error')
        try:
            fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(feed.get('tick', {}).get('checked_at_utc'))).total_seconds() < 180
        except (TypeError, ValueError):
            fresh = False
        if feed.get('paused'):
            badge, color = 'PAUSED · new batches disabled', 'yellow'
        elif active and not process_error:
            badge, color = 'RUNNING · batch process observed', 'green'
        elif active is not None and checked and not process_error and fresh:
            badge, color = 'WAITING · no batch observed', 'cyan'
        else:
            badge, color = 'CHECKING · process / heartbeat unavailable', 'yellow'
        latest = default_quarter(datetime.now(ZoneInfo('Asia/Kolkata')).date())
        parts = [line(f'{q} · {badge}' if short else f'{label(q)} ({q})  ·  {badge}', 'bold ' + color),
                 line('Queue census: ' + when(inspection.get('quarter_at')) + ' · selected reporting period', 'dim')]
        if q != latest:
            parts.append(line(f'Latest completed quarter: {latest} · c to select', 'dim'))
        notices = []
        for name, value in feed.get('blockers', {}).items():
            reason = (value.get('error') or value.get('reason') or f"Stopped with exit {value.get('exit_code', 'unknown')}") if isinstance(value, dict) else str(value)
            if 'Production sentinel mismatch' in reason:
                reason = 'Document drive identity check failed. Downloads are paused pending repair.'
            notices.append(f'NEEDS ATTENTION · {name.removesuffix(".stopped.json")}: {reason}')
        if inspection.get('quarter_error'):
            notices.append(('Showing saved data; refresh will retry. ' if data else 'Refresh deferred: ') + inspection['quarter_error'])
        if process_error:
            notices.append('Process check: ' + process_error)
        if feed.get('schedule', {}).get('error'):
            notices.append('Schedule: ' + feed['schedule']['error'])
        if notices:
            # One bounded line preserves controls on a 24-row terminal.
            parts.append(line(' | '.join(notices), 'yellow') if short else Text('\n'.join(notices), style='yellow'))
        queues = table(['Pipeline', 'Queued', 'Claimed*', 'Saved', 'Skipped', 'Retry', 'Failed'])
        definitions = [
            ('Financials', 'screener_company_queue', {'accepted'}),
            ('Downloads', 'artifact_download_queue', {'succeeded'}),
            ('Ratings', 'credit_rating_download_queue', {'succeeded'}),
        ]
        for name, key, saved in definitions:
            rows = data.get('queues', {}).get(key)
            def count(states, style=''):
                if rows is None:
                    return line('—', 'dim')
                return line(f"{sum(r['n'] for r in rows if r['state'].lower() in states):,}", style)
            queues.add_row(line(name, 'bold'), count({'pending'}, 'cyan'), count({'claimed', 'leased', 'running'}, 'magenta'),
                           count(saved, 'green'), count({'skipped_already_accepted', 'skipped_already_have_content', 'skipped_policy'}, 'blue'),
                           count({'failed_retryable', 'retryable_failure'}, 'yellow'),
                           count({'failed', 'failed_terminal', 'terminal_failure', 'manual_review', 'interaction_required'}, 'red'))
        parts.append(queues if short else Panel(queues, title='PIPELINE QUEUES', border_style='blue', box=box.ROUNDED))
        parts.append(line('*Saved queue state, not proof of a live worker · Retry = past failure', 'dim'))
        c, ac = data.get('coverage', {}), data.get('acquisition', {})
        def number(mapping, key):
            value = mapping.get(key)
            return f'{value:,}' if isinstance(value, int) else '—'
        parts.append(line(f"Financial companies · standalone {number(c, 'standalone')} · consolidated {number(c, 'consolidated')} · both {number(c, 'both_scopes')}", 'green'))
        parts.append(line(f"Documents · links {number(ac, 'document_links_discovered')} · stored {number(ac, 'documents_already_stored')} · processed {number(ac, 'document_processing_completed')}", 'cyan'))
        processing = data.get('processing')
        parts.append(line('Processing · ' + (' · '.join(f"{r['state'].replace('_', ' ')} {r['n']:,}" for r in processing) if processing else 'no content states observed'), 'dim'))
        if not data:
            parts.append(line('Quarter data unavailable; no lifetime counts substituted.', 'yellow'))
        unknown = ac.get('unknown_period_documents_for_quarter_companies', 0)
        if unknown and q not in ('all', 'unknown'):
            parts.append(line(f'{unknown:,} unassigned in company history (some non-quarterly) · /monitor --quarter unknown', 'yellow'))
        live = ', '.join(r.get('receipt_stage') or r.get('container', '?') for r in active or [])
        hints = [j.get('current_symbol') for j in feed.get('jobs', []) if not j.get('complete') and j.get('current_symbol')]
        parts.append(line('Workers · ' + (live if live and not process_error else 'none observed' if checked and active == [] and not process_error else 'check pending / unavailable') + ' · ' + when(checked), 'bold'))
        if hints and not short:
            parts.append(line('Last unfinished request (receipt only): ' + ', '.join(dict.fromkeys(hints)), 'dim'))

        financial = table(['Company', 'Quarter / scope', 'Outcome', 'Observed'])
        records = financial_rows(feed, data, q)
        for symbol, period, scope, outcome, updated, source in records[:1 if short else 4 if self.compact else 10]:
            financial.add_row(line(symbol), line(f'{period} / {scope}'), state(outcome), line(when(updated) + ' · ' + source, 'dim'))
        if not records:
            financial.add_row(line('No financial outcomes observed'), '', '', '')
        documents = table(['Company', 'Type', 'Download', 'Processing'])
        downloads = data.get('downloads', [])
        for row in downloads[:1 if short else 4 if self.compact else 10]:
            documents.add_row(line(row.get('current_legal_name')), line(row.get('artifact_type', '').replace('_', ' ')),
                              state(row.get('status') or row.get('acquisition_status')), state(row.get('extraction_status')))
        if not downloads:
            documents.add_row(line('No documents assigned to this quarter'), '', '', '')
        if short:
            if records:
                row = records[0]
                parts.append(line(f'Financial · {row[0]} · {row[1]} / {row[2]} · {state(row[3]).plain}', 'green'))
            else:
                parts.append(line('Financial · no recent outcomes for this quarter', 'dim'))
            if downloads:
                row = downloads[0]
                parts.append(line(f"Document · {row.get('current_legal_name')} · {row.get('artifact_type')} · {state(row.get('status') or row.get('acquisition_status')).plain}", 'cyan'))
        else:
            panels = [Panel(financial, title='FINANCIAL SCRAPING · recent outcomes', border_style='green', box=box.ROUNDED),
                      Panel(documents, title='DOCUMENTS & CREDIT RATINGS', border_style='cyan', box=box.ROUNDED)]
            if options.max_width >= 120:
                columns = Table.grid(expand=True, padding=(0, 1))
                columns.add_column(ratio=1)
                columns.add_column(ratio=1)
                columns.add_row(*panels)
                parts.append(columns)
            else:
                parts.extend(panels)
        schedule = feed.get('schedule', {})
        upcoming = sorted([j for j in schedule.get('jobs', []) if j.get('next_at_ist')], key=lambda j: j['next_at_ist'])
        next_job = upcoming[0] if upcoming else schedule.get('next_scheduled_batch') or {}
        parts.append(line('Next slot: ' + str(next_job.get('job', 'not observed')) + ' · ' + when(next_job.get('next_at_ist')), 'cyan'))
        expiry = schedule.get('document_expires_at_utc')
        storage = (inspection.get('raw') or {}).get('storage') or {}
        free = storage.get('free_bytes')
        parts.append(line('My Passport: ' + (f'{free / 1e9:.1f} GB free' if free is not None else 'capacity not checked'), 'dim'))
        parts.append(line('Document window: ' + ('EXPIRED' if schedule.get('document_window_expired') else when(expiry)), 'yellow' if schedule.get('document_window_expired') else 'dim'))
        if not short:
            parts.append(line('Quarter coverage counts stored companies; discovery may be incomplete. /coverage for details.', 'dim'))
            parts.append(line('Document list: first 40 artifacts, --offset to page · /company TICKER for statements', 'dim'))
        controls = 'Ctrl+C back · r refresh · c latest · [ ] quarter · q pick · a all · u unknown'
        parts.append(line(controls, 'dim'))
        yield Panel(Group(*parts), title='TRADING AI · LIVE SCRAPER MONITOR', border_style='cyan', padding=(0, 1), box=box.ROUNDED)
