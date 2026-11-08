"""Reconcile real browser series data against embedded payloads and retained exports."""
import importlib.util
from pathlib import Path
from .repair import ROOT, OUT, T, N
spec=importlib.util.spec_from_file_location('independent_chart_helpers', ROOT/'artifacts/technical-audit-v1/code/audit.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
helper.OUT=OUT
payload=helper.payload; js=helper.js; write=helper.write
import re,json


def run():
    data=js(OUT/'evidence/BROWSER_RESULTS.json');rows=[]
    def record(chart,day,field,a,e,source,tol=1e-8):
        if a is None or e is None:ok=a is None and e is None;delta=None
        else:delta=float(a)-float(e);ok=abs(delta)<=tol
        rows.append(dict(chart=chart,date=day,field=field,plotted=a,underlying=e,signed_difference=delta,tolerance=tol,status='MATCH' if ok else 'MISMATCH',evidence=source))
    for x in data['results']:
        if 'selected' in x:
            r=x['selected']
            for v in x['plotted']:
                f=v['field'];d=v['plotted']
                if f=='price' and 'index_close' not in r:
                    for f in ('open','high','low','close'):record(x['kind'],r['date'],f,d.get(f),r[f],x['chart'])
                else:
                    key='index_close' if f=='price' else f
                    key={'volume':'pct_above_ema20','return_20s_pct':'normalized_subsector'}.get(key,key) if 'index_close' in r else key
                    if key=='tech_activity':key='tech.value'
                    e=r
                    for k in key.split('.'):e=e.get(k) if isinstance(e,dict) else None
                    record(x['kind'],r['date'],f,d.get('value') if d else None,e,x['chart'])
            for f in ('daily_range','atr14','dtr_atr_pct'):
                if f in r:
                    patterns={'daily_range':r'DTR ([\d,.]+) INR','atr14':r'ATR14 RMA ([\d,.]+) INR','dtr_atr_pct':r'DTR / ATR ([\d,.]+)%'}
                    m=re.search(patterns[f],x['readouts']['dtr-box'] or '')
                    record(x['kind'],r['date'],f,float(m[1].replace(',','')) if m else None,r[f],'Actual DOM dtr-box numeric text',.0051 if f!='dtr_atr_pct' else 0)
            rows.append(dict(chart=x['kind'],date=x.get('screenshotDate'),field='screenshot_date',status='MATCH' if x.get('screenshotDate')==r['date'] else 'MISMATCH',evidence='screenshots/2026-11-05-'+x['kind']+'-'+r['date']+'.png'))
        elif x['kind']=='sector-rotation':
            z=x['data'];rr=payload(T/'charts/sector-rotation.html','rotation-data')['rows']
            names={r['official_name']:r['instrument_key'] for r in rr}
            for series in z['series']:
                orig=[r for r in rr if r['instrument_key']==names[series['title']]];base=orig[0]['close']
                for a,b in zip(series['data'],orig):record('sector-rotation:'+series['title'],b['date'],'rebased_close',a.get('value'),1000*b['close']/base, 'Actual browser line data against independently rebased retained close')
            for r in z['selectedRows']:
                if r['instrument_key']=='NSE_INDEX|Nifty Auto':
                    a=z['rs'][0][-1];record('sector-rotation',z['selected'],'rs55',a.get('value'),r['rs55'],'Actual browser RS series, explicitly Nifty50 benchmark')
        elif 'bars' in x:
            plotted=x['series'][0];bars=x['bars'];n=0;err=0
            # Read original API bodies independently of the browser's own payload.
            original={}
            for b in bars:
                source=b['source_file']
                if source not in original:original[source]={r[0]:r for r in json.loads((ROOT/source).read_text())['data']['candles']}
            for b in bars:
                raw=original[b['source_file']][b['timestamp']]
                for j,f in enumerate(('open','high','low','close'),1):
                    if float(b[f])!=float(raw[j]):raise AssertionError((b['timestamp'],f))
            for a,b in zip(plotted,bars):
                for f in ('open','high','low','close'):
                    delta=float(a[f])-float(b[f]);err=max(err,abs(delta));n+=1
            rows.append(dict(chart=x['kind'],field='all_OHLC_fields',plotted=len(plotted),underlying=len(bars),signed_difference=err,tolerance=0,status='MATCH' if len(plotted)==len(bars) and err==0 else 'MISMATCH',evidence=f'{n} numerical fields from actual Chart API series.data()'))
            for i in (0,len(bars)//2,len(bars)-1):
                for f in ('open','high','low','close'):record(x['kind'],bars[i]['timestamp'],f,plotted[i][f],bars[i][f],bars[i]['source_file'],0)
    write('CHART_VALUE_RECONCILIATION.csv',rows)
    summary=dict(rows=len(rows),mismatches=sum(r['status']=='MISMATCH' for r in rows),javascript_errors=len(data['errors']),remote_requests=len(data['remote']))
    (OUT/'evidence/RECONCILIATION_RESULTS.json').write_text(json.dumps(summary,indent=2)+'\n')
    assert summary['mismatches']==summary['javascript_errors']==summary['remote_requests']==0
    print(summary)
if __name__=='__main__':run()
