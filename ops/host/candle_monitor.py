"""Read candle progress from local batch receipts without opening the archive."""
import json
from datetime import datetime
from pathlib import Path


STATE = Path(__file__).resolve().parents[2] / 'artifacts/historical-candle-archive-v1/hcd02'


class CandleFeed:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else STATE
        self.fingerprint = None
        self.inventory = None

    def sample(self):
        path = self.root / 'full-inventory.json'
        try:
            stat = path.stat()
            fingerprint = (stat.st_mtime_ns, stat.st_size)
            if fingerprint != self.fingerprint:
                data = json.loads(path.read_text())
                rows = data['timeframes']
                counts, companies = {}, set()
                expected = successful = failed = 0
                for row in rows:
                    count = row['source_rows_returned']
                    values = [count, row['expected_requests'], row['successful_requests'], row['failed_requests']]
                    if any(type(value) is not int or value < 0 for value in values):
                        raise ValueError('Invalid candle count')
                    counts[row['timeframe']] = counts.get(row['timeframe'], 0) + count
                    if count:
                        companies.add(row['company_id'])
                    expected += row['expected_requests']
                    successful += row['successful_requests']
                    failed += row['failed_requests']
                complete, target = data['companies_with_all_four_planned_native_histories'], data['expected_companies']
                if any(type(value) is not int or value < 0 for value in (complete, target)):
                    raise ValueError('Invalid company count')
                if complete > target or successful + failed > expected:
                    raise ValueError('Invalid candle progress')
                self.inventory = dict(inventory_at=data['created_at'], source_rows=sum(counts.values()),
                    rows_by_timeframe=counts, companies_with_history=len(companies),
                    complete_companies=complete, expected_companies=target,
                    successful_requests=successful, expected_requests=expected, failed_requests=failed)
                self.fingerprint = fingerprint
            result = dict(self.inventory)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Never turn missing or malformed evidence into a zero count.
            return dict(available=False)
        try:
            campaign = json.loads((self.root / 'full-campaign.json').read_text())
            result['campaign_status'] = str(campaign['status'])
        except (OSError, ValueError, KeyError, TypeError):
            result['campaign_status'] = 'UNKNOWN'
            campaign = {}
        try:
            if campaign.get('phase') not in ('NATIVE', 'SESSION_REPAIRS'):
                raise ValueError('No active manifest phase')
            kind = 'special-full' if campaign.get('phase') == 'SESSION_REPAIRS' else 'full'
            execution = json.loads((self.root / f'{kind}-execution.json').read_text())
            run = execution['runs'][-1]
            # Old receipts never describe the current worker's activity.
            if datetime.fromisoformat(run['started_at']) < datetime.fromisoformat(campaign['started_at']):
                raise ValueError('Previous campaign run')
            checked, total, reused = execution['requests_checked'], execution['manifest_requests'], run['reused']
            resumed = run.get('resumed', 0)
            if any(type(value) is not int or value < 0 for value in (checked, total, reused, resumed)) or reused + resumed > checked or checked > total:
                raise ValueError('Invalid verification progress')
            stage = execution['stage']
            if stage not in ('VERIFYING_SAVED_DATA', 'RESUMING_COMPLETED', 'ACQUIRING', 'PAUSED', 'STOPPED',
                             'MANIFEST_PASS_FINISHED', 'FINISHED_WITH_ISOLATED_FAILURES'):
                raise ValueError('Unknown activity')
            result['activity'] = dict(stage=stage, checked=checked, total=total, reused=reused, resumed=resumed,
                                      observed_at=execution['updated_at'], scope=kind)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            pass
        return dict(available=True, **result)


def summary_lines(data, when, short=False):
    if not data or not data.get('available'):
        return ['Candles · inventory unavailable']
    complete, target = data['complete_companies'], data['expected_companies']
    successful, expected = data['successful_requests'], data['expected_requests']
    percent = f'{complete / target:.1%}' if target else '—'
    lines = [f"Candles · {data['source_rows']:,} source rows · {data['companies_with_history']:,} companies · {data['campaign_status']}",
             f'Candles · {complete:,}/{target:,} complete ({percent}) · requests {successful:,}/{expected:,} OK · {data["failed_requests"]:,} failed']
    if short:
        lines[1] = f'{complete:,}/{target:,} complete ({percent}) · {successful:,}/{expected:,} requests OK · {data["failed_requests"]:,} failed'
    if not short:
        lines.append('Candle rows · ' + ' · '.join(f'{name} {count:,}' for name, count in data['rows_by_timeframe'].items()))
    lines.append('Candle inventory: ' + when(data['inventory_at']) + ' · batch snapshot')
    if activity := data.get('activity'):
        stage = activity['stage'].replace('_', ' ').lower()
        activity_line = (f"Candles · {stage} · {activity['checked']:,}/{activity['total']:,} checked · "
                         f"{activity['resumed']:,} resumed · {when(activity['observed_at'])}")
        lines.append(activity_line)
    return lines
