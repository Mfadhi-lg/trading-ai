"""Read-only publisher probes for the operator-directed rating recovery."""
import argparse
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', action='append', required=True)
    parser.add_argument('--artifact-id', type=int)
    args = parser.parse_args()
    conn = sqlite3.connect('file:/data/scraper.db?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for host in args.host:
            row = conn.execute('''SELECT q.artifact_id,q.requested_url,c.current_legal_name
                FROM credit_rating_download_queue q JOIN document_artifacts a USING(artifact_id)
                JOIN companies c USING(company_id) WHERE q.target_host=?
                AND NOT EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=q.artifact_id)
                AND (? IS NULL OR q.artifact_id=?)
                ORDER BY q.download_job_id LIMIT 1''', (host, args.artifact_id, args.artifact_id)).fetchone()
            if row is None:
                continue
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            downloads = []
            pdfs = []
            page.on('download', lambda d: downloads.append(d))
            def capture(r):
                if r.headers.get('content-type', '').startswith('application/pdf'):
                    pdfs.append(r.url)
            page.on('response', capture)
            result = dict(row)
            try:
                response = page.goto(row['requested_url'], wait_until='domcontentloaded', timeout=45000)
                result['status'] = response.status if response else None
                page.wait_for_timeout(15000 if args.artifact_id else 3000)
            except Exception as e:
                result['navigation_error'] = str(e)[:300]
            result['final_url'] = page.url
            result['pdf_responses'] = pdfs
            result['downloads'] = [d.url for d in downloads]
            try:
                result['title'] = page.title()
                result['visible_text'] = page.locator('body').first.inner_text(timeout=3000)[:2500]
                result['controls'] = page.locator('a,button,input[type=button],input[type=submit]').evaluate_all(
                    "els => els.filter(e => e.getBoundingClientRect().width > 0).map(e => ({text:(e.innerText || e.value || '').trim().slice(0,100),href:e.getAttribute('href'),onclick:e.getAttribute('onclick')})).filter(e => /download|pdf|print|rationale|report/i.test(e.text+' '+e.href)).slice(0,20)"
                )
            except Exception as e:
                result['inspection_error'] = str(e)[:300]
            print(json.dumps(result), flush=True)
            context.close()
        browser.close()


if __name__ == '__main__':
    main()
