"""Test the same finite overlays used by the production financial adapter."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OVERLAYS=('scrapper/db/screener_discovery_repo.py','scrapper/commands/collection_scope.py',
    'scrapper/config.py','scrapper/sources/screener/document_links.py',
    'scrapper/workflows/screener/ingest.py','scrapper/pipeline/accepted_document_downloads.py',
    'scrapper/documents/discovery/promotion.py','scrapper/db/document_download_queue_repo.py')


def main():
    args=['docker','run','--rm','--pull=never','--network=none','--read-only','--tmpfs','/tmp:rw,nosuid,size=512m']
    for path in OVERLAYS:args+=['--mount',f'type=bind,source={ROOT/path},target=/app/{path},readonly']
    for key,value in {'PYTHONDONTWRITEBYTECODE':'1','TRADING_AI_ENV':'test',
        'SCRAPER_DB_PATH':'/tmp/store/scraper.db','SCRAPER_STORE_PATH':'/tmp/store',
        'SCRAPER_SNAPSHOTS_PATH':'/tmp/store/snapshots','SCRAPER_DOCUMENT_STORE_PATH':'/tmp/store/snapshots',
        'SCRAPER_FILES_PATH':'/tmp/store/files','SCRAPER_EXPORTS_PATH':'/tmp/store/exports'}.items():args+=['--env',key+'='+value]
    args+=['--entrypoint','python','sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9',
        '-B','-m','unittest','scrapper.tests.test_screener_vertical']
    result=subprocess.run(args,capture_output=True,text=True,timeout=90)
    out=ROOT/'artifacts/acquisition-actual-download-recovery/raw/financial-overlay-regression.log'
    out.write_text(result.stdout+result.stderr)
    print(result.stdout[-500:]+result.stderr[-1000:])
    result.check_returncode()


if __name__=='__main__':main()
