def main():
    """Finite user-requested Screener screen intake. Shared host pacing; no financial writes."""
    from pathlib import Path
    import sys,json
    sys.path[:0]=['/activation','/app']
    from scrapper.db.database import connect,check_schema_compatibility
    from scrapper.db.repo import utc_now
    from scrapper.operations.workers.screener_requests import SharedScreenerClient
    from nse_results_runtime import run_owned
    from bs4 import BeautifulSoup
    DB=Path('/data/scraper.db')
    run_owned((),DB,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
    c=connect(DB);check_schema_compatibility(c)
    client=SharedScreenerClient(c,owner='authorized-screen-3844820',wait_seconds=10)
    try:
     url='https://www.screener.in/screens/3844820/new-results/'
     response=client._get(url)
     Path('/screen-evidence/page-1.html').write_bytes(response.content)
     if response.status_code!=200:raise RuntimeError(f'Screen returned HTTP {response.status_code}')
     soup=BeautifulSoup(response.content,'html.parser')
     print(json.dumps({'stage':'screen_observed','url':url,'http_status':response.status_code,
      'title':soup.title.get_text(' ',strip=True) if soup.title else None,
      'query_fields':[{'name':n.get('name'),'id':n.get('id'),'value':n.get('value') or n.get_text(' ',strip=True)} for n in soup.select('textarea,input[name=query],input[name=sort]')],
      'headers':[n.get_text(' ',strip=True) for n in soup.select('table thead th')],
      'pagination':[n.get_text(' ',strip=True) for n in soup.select('.pagination,.options')][:3],
      'requests':1}),flush=True)
    finally:client.close();c.close()

if __name__=='__main__':main()
