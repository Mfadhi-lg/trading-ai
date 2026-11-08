"""Freeze, inspect or execute a finite reviewed June/browser extraction campaign."""
import argparse
import json
from pathlib import Path
from ..config import DB_PATH
from ..documents.ocr import TesseractOcr
from ..pipeline.extract_document import processing_version
from ..operations.workers.processing_campaign import (
    freeze_campaign, digest, read_connection, validate_campaign, campaign_status, run_campaign)
from ..operations.workers.lifecycle import run_bounded
from .quarter_extract import pending_contents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest='action', required=True)
    freeze = sub.add_parser('freeze')
    freeze.add_argument('--campaign-id', required=True)
    scope = freeze.add_mutually_exclusive_group(required=True)
    scope.add_argument('--period')
    scope.add_argument('--run-id')
    freeze.add_argument('--browser-job-id', type=int, action='append', default=[])
    freeze.add_argument('--limit', type=int, default=100)
    for action in ('status', 'run'):
        command = sub.add_parser(action)
        command.add_argument('--manifest', type=Path, required=True)
        command.add_argument('--sha256', required=True)
        if action=='run':
            command.add_argument('--output-dir', type=Path, required=True)
            command.add_argument('--limit', type=int, default=100)
            command.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args(argv)
    engine = TesseractOcr()
    if args.action=='freeze':
        conn = read_connection(args.db)
        try:
            campaign = freeze_campaign(conn,campaign_id=args.campaign_id,
                version=processing_version(engine),period=args.period,run_id=args.run_id,
                browser_job_ids=args.browser_job_id,limit=args.limit,quarter_selector=pending_contents)
        finally:
            conn.close()
        print(json.dumps({'sha256':digest(campaign), 'manifest':campaign},sort_keys=True))
    else:
        campaign = json.loads(args.manifest.read_text())
        if args.action=='status':
            conn=read_connection(args.db)
            try:
                # Inspect the frozen version without needing its OCR binary installed.
                from ..db.documents.processing import ProcessingVersion
                validate_campaign(conn,campaign,expected_sha256=args.sha256,
                    version=ProcessingVersion(**campaign['version']))
                report=campaign_status(conn,campaign)
            finally:
                conn.close()
        else:
            report=run_bounded(run_campaign,db_path=args.db,campaign=campaign,
                expected_sha256=args.sha256,ocr_engine=engine,output_dir=args.output_dir,limit=args.limit)
        print(json.dumps(report,sort_keys=True))
        if args.action=='run' and report['outstanding']:
            raise SystemExit(2)


if __name__=='__main__':
    main()
