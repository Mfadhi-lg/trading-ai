"""Exact user screen, finite 14-page capture; canonical queue admission after completeness."""
def main():
    import sys
    sys.path[:0]=['/activation','/app']
    import json
    from pathlib import Path
    from dataclasses import replace
    from datetime import datetime,timezone
    from bs4 import BeautifulSoup
    from scrapper.db.database import connect,check_schema_compatibility
    from scrapper.db.repo import utc_now,start_run,finish_run,store_blob,record_fetch
    from scrapper.db.screener_discovery_repo import persist_screen,enqueue_complete_screen_pages
    from scrapper.sources.screener.result_screen import parse_result_screen,canonical_query
    from scrapper.core.http import FetchResponse
    from scrapper.operations.workers.screener_requests import SharedScreenerClient
    from scrapper.config import SNAPSHOTS,RESULT_SCREEN_PARSER_NAME,RESULT_SCREEN_PARSER_VERSION
    from nse_results_runtime import run_owned
    db=Path('/data/scraper.db');out=Path('/screen-evidence')
    if (out/'screen-sync.json').exists():raise RuntimeError('Complete sync receipt already exists; no replay')
    run_owned((),db,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
    query='Last result date >202608 AND Market Capitalization >580'
    base='https://www.screener.in/screens/3844820/new-results/'
    pages=[];responses=[]
    c=connect(db);check_schema_compatibility(c)
    client=SharedScreenerClient(c,owner='authorized-complete-screen-3844820',wait_seconds=10)
    try:
        for page in range(1,15):
            url=base if page==1 else base+'?page='+str(page)
            file=out/f'page-{page}.html'
            if file.exists():body=file.read_bytes()
            else:
                response=client._get(url)
                if response.status_code!=200:raise RuntimeError(f'Page {page}: HTTP {response.status_code}')
                body=response.content;file.write_bytes(body)
            soup=BeautifulSoup(body,'html.parser');field=soup.select_one('textarea[name=query]')
            if field is None or canonical_query(field.get_text())!=query:raise RuntimeError('Screen filter changed; no admission')
            parsed=parse_result_screen(body,query,url)
            if parsed.page_number!=page or parsed.page_count!=14 or parsed.result_count!=651:raise RuntimeError('Screen pagination/count changed; no admission')
            pages.append(parsed)
            responses.append(FetchResponse(url,url,200,body,'text/html','utf-8',None,None,'authenticated_screen_capture','ok'))
            print(json.dumps({'stage':'screen_page_captured','page':page,'pages':14,'rows':len(parsed.rows),'result_count':parsed.result_count}),flush=True)
        if len({r.screen_row_company_id for p in pages for r in p.rows})!=651:raise RuntimeError('Duplicate or missing screen companies')
        now=utc_now();run=start_run(c,'complete-screen-3844820',{'source':'screener','url':base,'pages':14,'result_count':651})
        try:
            ids=[]
            for page,response in zip(pages,responses):
                observed=datetime.fromtimestamp((out/f'page-{page.page_number}.html').stat().st_mtime,timezone.utc).isoformat()
                blob=store_blob(c,response,'_RESULT_DISCOVERY','september_3844820',observed,SNAPSHOTS,resource_kind='result_screen')
                fetch=record_fetch(c,run,None,response,'not_applicable',observed,blob,resource_kind='result_screen')
                sid,_=persist_screen(c,page,query_role='september_3844820',fetch_id=fetch,blob_id=blob,observed_at_utc=observed,parser_name=RESULT_SCREEN_PARSER_NAME,parser_version=RESULT_SCREEN_PARSER_VERSION)
                ids.append(sid)
            result=enqueue_complete_screen_pages(c,ids,now_utc=now)
            result.update(source_url=base,query=query,reporting_periods=sorted({r.result_period for p in pages for r in p.rows}))
            finish_run(c,run,'ok');(out/'screen-sync.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'stage':'complete_screen_sync',**result}),flush=True)
        except BaseException:
            c.rollback();finish_run(c,run,'error');raise
    finally:client.close();c.close()

if __name__=='__main__':main()
