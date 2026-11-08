"""One bounded document stage; caller owns scheduling and daily budgets."""
import argparse,json
from pathlib import Path
from ..config import DB_PATH
from ..operations.workers.automatic_documents import run_automatic_discovery,run_automatic_downloads


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('discovery','download'))
    p.add_argument('--db',type=Path,default=DB_PATH)
    p.add_argument('--epoch',required=True)
    p.add_argument('--limit',type=int,default=2)
    p.add_argument('--screener-request-limit',type=int,default=2)
    p.add_argument('--official-request-limit',type=int,default=2)
    p.add_argument('--historical-limit',type=int,default=0)
    a=p.parse_args()
    run=run_automatic_discovery if a.stage=='discovery' else run_automatic_downloads
    options = dict(screener_request_limit=a.screener_request_limit,
                   official_request_limit=a.official_request_limit) if a.stage=='discovery' else {}
    print(json.dumps(run(db_path=a.db,epoch=a.epoch,limit=a.limit,historical_limit=a.historical_limit,**options),sort_keys=True))


if __name__=='__main__': main()
