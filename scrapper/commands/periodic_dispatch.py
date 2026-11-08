"""Explicit opt-in periodic dispatch; existing manual CLI remains compatible."""
import argparse
import json
from pathlib import Path
from ..config import DB_PATH


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DB_PATH)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    if not args.apply:
        parser.error('periodic dispatch requires --apply')
    from ..operations.coordinator.periodic import schedule_tick,run_pending,serve
    if args.once:
        schedule_tick(db_path=args.db)
        result=run_pending(db_path=args.db)
    else:
        result=serve(db_path=args.db)
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
