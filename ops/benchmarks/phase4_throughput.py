"""Offline census/benchmark. Production DB and originals MUST be mounted read-only.

Launch with the existing host owner lock/gate and SAFE_EMPTY_WAL guard. No HTTP,
production SQL writes, schema changes, archive writes or Sacheerome replay.
"""
import hashlib, json, multiprocessing as mp, os, resource, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path[:0] = ['/app', '/activation']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot


def bench(row, output):
    from scrapper.documents.pdf import extract_pdf
    from scrapper.documents.powerpoint import extract_powerpoint
    from scrapper.documents.ocr import TesseractOcr
    start = time.monotonic(); cpu = time.process_time()
    try:
        body = Path(row['local_path']).read_bytes(); read = time.monotonic()
        assert len(body) == row['byte_size']
        assert hashlib.sha256(body).hexdigest() == row['content_sha256']
        verified = time.monotonic()
        engine = TesseractOcr()
        extracted = (extract_pdf(body, ocr_engine=engine) if row['media_type']=='application/pdf'
                     else extract_powerpoint(body, media_type=row['media_type'], ocr_engine=engine))
        parsed = time.monotonic(); encoded = json.dumps(extracted.to_dict()); end = time.monotonic()
        result = {**row, 'status':extracted.status, 'pages':len(extracted.pages),
            'native_pages':extracted.native_page_count, 'ocr_pages':extracted.ocr_page_count,
            'unusable_pages':extracted.unusable_page_count, 'elements':sum(len(p.elements) for p in extracted.pages),
            'read_seconds':read-start, 'hash_seconds':verified-read, 'parse_seconds':parsed-verified,
            'serialize_seconds':end-parsed, 'total_seconds':end-start,
            'cpu_seconds':time.process_time()-cpu, 'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'serialized_bytes':len(encoded.encode())}
    except Exception as exc: result={**row, 'status':'error', 'error':str(exc), 'total_seconds':time.monotonic()-start}
    Path(output).write_text(json.dumps(result))


def batch(rows, label):
    started=time.monotonic(); jobs=[]
    for row in rows:
        output=f'/tmp/{label}-{row["content_id"]}.json'
        p=mp.Process(target=bench,args=(row,output));p.start();jobs.append((p,output,row))
    results=[]
    for p,output,row in jobs:
        p.join(max(0,60-(time.monotonic()-started)))
        if p.is_alive():p.terminate();p.join();results.append({**row,'status':'timeout_60s'})
        elif Path(output).exists():results.append(json.loads(Path(output).read_text()))
        else:results.append({**row,'status':'child_failed','exitcode':p.exitcode})
    return {'label':label,'wall_seconds':time.monotonic()-started,'results':results}


def main():
    started=time.monotonic();db=Path('/data/scraper.db');before=assert_safe_empty_wal(db)
    c=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    def q(sql):return [dict(r) for r in c.execute(sql)]
    data={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'source_requests':0,'production_writes':0,
          'cpu_count':os.cpu_count(),'memory':Path('/proc/meminfo').read_text().splitlines()[:3],
          'cgroup_cpu':Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
          'cgroup_memory':Path('/sys/fs/cgroup/memory.max').read_text().strip()}
    if '--metrics-only' in sys.argv:
        from collections import defaultdict, Counter
        from urllib.parse import urlsplit
        groups=defaultdict(list)
        for r in q('SELECT requested_url,http_status,result_status,elapsed_ms,byte_size,started_at_utc,error_json FROM artifact_download_attempts'):
            groups[urlsplit(r['requested_url']).hostname or 'unknown'].append(r)
        metrics=[]
        for host,items in groups.items():
            times=sorted(r['elapsed_ms'] for r in items)
            metrics.append({'host':host,'attempts':len(items),'statuses':dict(Counter(str(r['http_status']) for r in items)),
                'results':dict(Counter(r['result_status'] for r in items)),
                'mean_elapsed_ms':sum(times)/len(times),'p95_elapsed_ms':times[max(0,__import__('math').ceil(len(times)*.95)-1)],
                'bytes':sum(r['byte_size'] or 0 for r in items),'first':min(r['started_at_utc'] for r in items),
                'last':max(r['started_at_utc'] for r in items),
                'resets':sum('reset' in r['error_json'].lower() for r in items)})
        data['historical_download_attempts_by_host']=sorted(metrics,key=lambda x:x['attempts'],reverse=True)
        data['meaning']='Retained attempt ledger, not all HTTP hops; legacy null-status validated imports are not proven network requests. Not a controlled load experiment.'
        c.close();assert snapshot(db)==before['files']
        Path('/output/metrics.json').write_text(json.dumps(data,indent=2));return
    tables=('artifact_download_attempts','credit_rating_resolution_attempts','fetch_observations','document_extractions','acquisition_host_state','artifact_download_host_health','credit_rating_host_health')
    data['columns']={t:q('PRAGMA table_info('+t+')') for t in tables}
    data['host_state']=q('SELECT * FROM acquisition_host_state WHERE blocked_until>0 OR circuit_until>0 OR consecutive_failures>0')
    data['legacy_health']=q('SELECT * FROM artifact_download_host_health WHERE consecutive_blocks>0 OR blocked_until_utc IS NOT NULL')
    data['host_policy']=q('SELECT * FROM credit_rating_host_policy')
    base='''SELECT c.content_id,c.artifact_id,c.media_type,c.content_sha256,c.byte_size,b.local_path,a.artifact_type,
        EXISTS(SELECT 1 FROM screener_collection_scope s WHERE s.company_id=a.company_id AND s.eligibility='eligible') eligible
        FROM artifact_contents c JOIN document_artifacts a USING(artifact_id) JOIN response_blobs b USING(blob_id)
        WHERE a.artifact_type IN ('presentation','transcript') AND c.extraction_status='not_started' ORDER BY c.content_id'''
    rows=q(base); physical={'registered':len(rows),'present_size_match':0,'missing':0,'wrong_size':0,'unsupported_path':0,'eligible_present':0,'registered_bytes':sum(r['byte_size'] for r in rows)}
    for row in rows:
        path=Path(row['local_path'])
        if not str(path).startswith('/documents/'):physical['unsupported_path']+=1;continue
        try: size=path.stat().st_size
        except FileNotFoundError:physical['missing']+=1;continue
        if size!=row['byte_size']:physical['wrong_size']+=1;continue
        physical['present_size_match']+=1;physical['eligible_present']+=row['eligible']
    data['physical_backlog']=physical
    data['network_pending']=q('''SELECT a.artifact_type,count(*) jobs,count(DISTINCT a.artifact_id) artifacts FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
        WHERE a.artifact_type IN ('presentation','transcript') AND q.status IN ('PENDING','RETRYABLE_FAILURE','LEASED')
        AND NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id) GROUP BY 1''')
    data['download_status']=q('''SELECT http_status,result_status,count(*) n FROM artifact_download_attempts GROUP BY 1,2''')
    data['download_errors']=q('''SELECT error_json,count(*) n FROM artifact_download_attempts WHERE error_json<>'{}' GROUP BY 1 ORDER BY n DESC LIMIT 25''')
    data['recent_downloads']=q('SELECT * FROM artifact_download_attempts ORDER BY download_attempt_id DESC LIMIT 12')
    data['canary_extraction']=q('SELECT * FROM document_extractions WHERE extraction_id=33641')
    c.close()
    assert snapshot(db)==before['files'],'Production changed during census'
    data['census_seconds']=time.monotonic()-started
    Path('/output/census.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({'stage':'census_complete',**physical}),flush=True)
    if '--census-only' in sys.argv: return
    # Deterministic spread over each document class, including one PPTX.
    selected=[]
    for kind in ('transcript','presentation'):
        group=[r for r in rows if r['artifact_type']==kind and r['media_type']=='application/pdf' and Path(r['local_path']).is_file()]
        selected.extend(group[int((len(group)-1)*f)] for f in (0,.33,.66,1))
    selected.extend(r for r in rows if r['media_type']!='application/pdf')
    results=[]
    for i,row in enumerate(selected):
        result=batch([row],f'serial-{i}');results.append(result);print(json.dumps(result),flush=True)
    good=[x['results'][0] for x in results if x['results'][0]['status'] in ('ok','partial')]
    pair=sorted(good,key=lambda x:x['total_seconds'],reverse=True)[:2]
    if len(pair)==2:
        results.append(batch(pair,'pair'));print(json.dumps(results[-1]),flush=True)
    data['benchmarks']=results
    data['benchmark_limits']='60s/document; sample is deterministic, small, cache-sensitive; no production persistence benchmark'
    assert snapshot(db)==before['files'],'Production changed while offline benchmarking'
    Path('/output/report.json').write_text(json.dumps(data,indent=2))

if __name__=='__main__':main()
