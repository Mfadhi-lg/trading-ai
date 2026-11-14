"""One bounded offline refresh from retained Passport data to local charts.

No acquisition, scheduling, production database access or deployment.
"""
import argparse
import hashlib
from pathlib import Path
from .production import build, save
from .frames_build import extend
from .chart_export import export


def seal(base):
    base=Path(base)
    files={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted(base.rglob('*')) if p.is_file() and p.name!='SHA256.json'}
    save(base/'SHA256.json',files)
    return files


def build_export(output, *, start, end, intraday_start, action_review=None,
                 incremental_state=None, reviewed_calendar=None):
    """Bounded maintained builder used by both CLI and approved refresh service."""
    base=build(output,start=start,end=end,action_review=action_review,
               incremental_state=incremental_state,reviewed_calendar=reviewed_calendar)
    extend(base,intraday_start=intraday_start)
    entry=export(base);seal(base)
    return entry


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--start',default='2025-12-01');p.add_argument('--end',default='2026-11-06')
    p.add_argument('--intraday-start',default='2026-08-17')
    p.add_argument('--action-review',type=Path)
    p.add_argument('--incremental-state',type=Path,
                   help='Explicit LIVE-FRESH receipt root; checked alongside retained histories')
    p.add_argument('--reviewed-calendar',type=Path,
                   help='Explicit verified calendar JSON for current source completion')
    a=p.parse_args()
    calendar=None
    if a.reviewed_calendar:
        from scrapper.market_refresh.service import load_calendar
        calendar=load_calendar(a.reviewed_calendar)
    entry=build_export(a.output,start=a.start,end=a.end,intraday_start=a.intraday_start,
                       action_review=a.action_review,incremental_state=a.incremental_state,reviewed_calendar=calendar)
    print(entry.resolve())


if __name__=='__main__':main()
