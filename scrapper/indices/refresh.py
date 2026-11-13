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


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--start',default='2025-12-01');p.add_argument('--end',default='2026-11-06')
    p.add_argument('--intraday-start',default='2026-08-17')
    p.add_argument('--action-review',type=Path)
    a=p.parse_args()
    base=build(a.output,start=a.start,end=a.end,action_review=a.action_review)
    extend(base,intraday_start=a.intraday_start)
    entry=export(base);seal(base)
    print(entry.resolve())


if __name__=='__main__':main()
