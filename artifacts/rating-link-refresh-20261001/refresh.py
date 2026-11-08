"""Refresh rating metadata only for companies in the failed agency job ledger.

Run inside the authenticated market-cap-monitor container. Source HTML and
verified document links are persisted; financial acceptance and download jobs
are deliberately outside this focused metadata refresh.
"""
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from scrapper.config import DB_PATH, EXPORTS, SNAPSHOTS
from scrapper.core.http import ScreenerHostClient
from scrapper.db.database import connect
from scrapper.db.repo import (
    finish_run, get_or_create_parse_artifact, record_fetch, start_run,
    store_blob, utc_now,
)
from scrapper.pipeline.promote_document_links import promote_document_links
from scrapper.sources.screener.company import _parse_documents, _parse_identity_and_quote

RECOVERY_RUN = 'browser-ratings-20261001'
FAILED_HOSTS = ('www.acuite.in', 'connect.acuite.in', 'www.infomerics.com')
CURRENT_HOSTS = set(FAILED_HOSTS) | {
    'acuite.in', 'infomerics.com', 'infomericstorage.blob.core.windows.net',
}
REPORT = EXPORTS / 'rating-link-refresh-20261001.json'


def normalized(url):
    return unquote(url or '').strip()


def selected_targets(conn):
    from scrapper.db.documents.recovery.rating_metadata_refresh import selected_targets as select
    return select(conn, recovery_run=RECOVERY_RUN, failed_hosts=FAILED_HOSTS)


def save_report(payload):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
    temporary.replace(REPORT)


def main():
    from scrapper.documents.recovery.rating_metadata_refresh import run_refresh
    return run_refresh(recovery_run=RECOVERY_RUN, failed_hosts=FAILED_HOSTS,
        current_hosts=CURRENT_HOSTS, db_path=DB_PATH, snapshots=SNAPSHOTS,
        report=REPORT, save_report=save_report,
        _hooks=sys.modules[__name__])


if __name__ == '__main__':
    main()
