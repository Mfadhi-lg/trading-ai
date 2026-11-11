"""Networkless monitor reader. SQL writes forbidden; only WAL sidecars writable.

The production directory must allow SQLite to create/read its shared WAL index.
Every database connection still uses URI mode=ro and PRAGMA query_only=ON.
"""
import json
from pathlib import Path
import sys
sys.path[:0]=['/host','/activation','/app']
ALLOWED={'quarter_monitor','quarter_options','quarter_coverage_export','summary',
         'search','company','company_report','company_catalog','financials','download_diagnostics'}

def main():
    action=sys.argv[1];kwargs=json.loads(sys.argv[2])
    if action=='census':
        from acquisition_db_status import census
        value=census(Path('/data/scraper.db'),include_summary=bool(kwargs.get('summary')),live=True)
    elif action in ALLOWED:
        from acquisition_queries import inspect
        kwargs.pop('live',None)
        value=inspect(Path('/data/scraper.db'),action,live=True,**kwargs)
    else:raise ValueError('Unsupported monitor query')
    print(json.dumps(value))

if __name__=='__main__':
    import fcntl
    with Path('/activation/.monitor-readers.lock').open('rb') as gate:
        fcntl.flock(gate,fcntl.LOCK_SH)
        try:main()
        finally:fcntl.flock(gate,fcntl.LOCK_UN)
