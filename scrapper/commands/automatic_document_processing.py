"""Bounded processing of inbox arrivals; historical extraction defaults to zero."""
import argparse
import json
from pathlib import Path
from ..config import DB_PATH
from ..documents.ocr import TesseractOcr
from ..operations.workers.document_processing import run_automatic_processing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DB_PATH)
    p.add_argument('--epoch',required=True)
    p.add_argument('--limit',type=int,default=2)
    p.add_argument('--historical-limit',type=int,default=0)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(run_automatic_processing(db_path=args.db,epoch=args.epoch,
        limit=args.limit,historical_limit=args.historical_limit,
        output_dir=args.output_dir,ocr_engine=TesseractOcr()),sort_keys=True))


if __name__ == '__main__': main()
