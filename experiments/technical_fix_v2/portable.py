"""Portable file:// review with every local HTML dependency and checksums."""
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
from urllib.parse import unquote,urlsplit
import zipfile
from experiments.historical_candle_archive_v1.storage import ROOT,sha,dump

OUT=ROOT/'artifacts/technical-fix-v2'

class References(HTMLParser):
    def __init__(self):super().__init__();self.refs=[]
    def handle_starttag(self,tag,attrs):
        self.refs.extend(v for k,v in attrs if k in ('src','href') and v)


def build(destination=None):
    destination=Path(destination or OUT/'portable-review')
    # The output is replaced atomically per file; never follow arbitrary paths.
    seeds=list((ROOT/'artifacts/technical-integration-v2/charts').rglob('*'))
    seeds=[p for p in seeds if p.is_file() and p.name!='template.html']
    seeds += [ROOT/'artifacts/technical-integration-v2/chart-preservation.json']
    seeds += [p for p in OUT.glob('*.json') if p.name!='PORTABLE_PACKAGE.json']+list(OUT.glob('*.csv'))+list(OUT.glob('*.log'))
    seeds += [OUT/'recurring/AUTHORIZATION_REQUEST.json',OUT/'recurring/OWNER_GRANT.json',OUT/'recurring/TIMER_STATUS.json',OUT/'CONTINUATION.md']
    hcd=ROOT/'artifacts/historical-candle-archive-v1/hcd02'
    seeds += [hcd/n for n in ('full-inventory.json','repair-queue.csv','repair-queue-summary.json','full-campaign.json','owner-authorization.json','hdfcbank-adjustment-review.json','identity-continuity-review.json')]
    todo=list(seeds);seen=set();missing=[]
    while todo:
        source=todo.pop().resolve()
        if source in seen:continue
        if not source.is_relative_to(ROOT):raise RuntimeError('External local dependency '+str(source))
        if not source.is_file():missing.append(str(source.relative_to(ROOT)));continue
        seen.add(source)
        if source.suffix=='.html':
            parser=References();parser.feed(source.read_text())
            for ref in parser.refs:
                uri=urlsplit(ref)
                if uri.scheme or uri.netloc or not uri.path:continue
                todo.append(source.parent/unquote(uri.path))
    if missing:raise RuntimeError('Missing portable dependencies: '+str(sorted(set(missing))))
    hashes={}
    for source in sorted(seen):
        rel=source.relative_to(ROOT);target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target);hashes[str(rel)]=sha(target.read_bytes())
    (destination/'artifacts/technical-fix-v2/screenshots').mkdir(parents=True,exist_ok=True)
    (destination/'OPEN.html').write_text('<!doctype html><meta charset="utf-8"><title>HCD-02 / TECH-FIX-02 review</title><h1>Passport chart review</h1><p>Extract the entire ZIP, then open this file. All scripts and data are local. Passport is not needed to view the exported data.</p><p><a href="artifacts/technical-integration-v2/charts/technical-system.html">Open technical charts</a></p><p>Three exported companies and twelve timeframe views are validation samples, not a claim of full-universe completion. Current request coverage and exceptions are included separately.</p><p><a href="artifacts/historical-candle-archive-v1/hcd02/full-inventory.json">Actual backfill coverage snapshot</a> · <a href="artifacts/historical-candle-archive-v1/hcd02/repair-queue.csv">Quarantined and unresolved records</a></p>')
    dump(destination/'PACKAGE.json',dict(files=len(hashes),sha256=hashes,local_html_dependencies_complete=True,archive_data_included=False,chart_data_assets_included=True))
    archive=OUT/'HCD-02-TECH-FIX-02-portable-review.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(destination.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(destination))
    result=dict(package=str(archive.relative_to(ROOT)),sha256=sha(archive.read_bytes()),files=len(hashes),bytes=archive.stat().st_size,entry='OPEN.html',all_local_dependencies_present=True)
    dump(OUT/'PORTABLE_PACKAGE.json',result);print(json.dumps(result));return destination

if __name__=='__main__':build()
