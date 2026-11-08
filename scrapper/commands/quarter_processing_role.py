"""Serve bounded deterministic processing for one explicitly configured quarter."""
import argparse
from pathlib import Path
from ..config import DB_PATH, CAMPAIGNS
from ..operations.workers.quarter_processing import run_role, validate_scope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DB_PATH)
    parser.add_argument('--period',required=True)
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--state-dir',type=Path)
    parser.add_argument('--apply',action='store_true',required=True)
    args = parser.parse_args(argv)
    try:
        validate_scope(args.period,args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    from ..documents.ocr import TesseractOcr
    from .quarter_extract import pending_contents
    run_role(db_path=args.db,period=args.period,limit=args.limit,
        state_dir=args.state_dir or CAMPAIGNS/('quarter-'+args.period),
        ocr_engine=TesseractOcr(),quarter_selector=pending_contents)


if __name__=='__main__':
    main()
