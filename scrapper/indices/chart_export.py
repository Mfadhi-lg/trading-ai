"""Portable chart export over a retained index build; no provider requests."""
import argparse
import json
from pathlib import Path
import shutil
from .production import ROOT

FIELDS=('date','start','end','index_close','ema10','ema20','rs55','pct_above_ema20','pct_rising','pct_rvol_gt1','traded_value_proxy_crore','aggregate_activity_ratio20','rising_count','falling_count','observed_constituents','expected_constituents','missing_members','missing_data_flag','return_1s_pct','rebalancing','available_at')


def export(base):
    base=Path(base);out=base/'charts';out.mkdir(exist_ok=True)
    read=lambda p:json.loads(p.read_text())
    daily=read(base/'datasets.json');official=read(base/'official.json');coverage=read(base/'coverage.json')
    membership=read(base/'membership.json');frames=read(base/'timeframes/datasets.json') if (base/'timeframes/datasets.json').exists() else {}
    def compact(rows):return [{k:v for k,v in r.items() if k in FIELDS} for r in rows]
    data={}
    for code,d in daily.items():
        data[code]=dict(members=d['members'],frames={'Daily':dict(rows=compact(d['daily']),leave_one_out={s:compact(r) for s,r in d['leave_one_out'].items()})})
        for tf,frame in frames.get(code,{}).items():
            data[code]['frames'][tf]=dict(rows=compact(frame['rows']),leave_one_out={s:compact(r) for s,r in frame['leave_one_out'].items()})
    benchmarks=read(base/'timeframes/benchmarks.json') if (base/'timeframes/benchmarks.json').exists() else {}
    from .timeframes import expected_grid
    weekly_grid=expected_grid(coverage['start'],coverage['end'],'Weekly',decision_time=coverage['created_at'])
    payload=dict(weekly_grid=weekly_grid,benchmarks=benchmarks,datasets=data,coverage=coverage,membership=membership,inventory=official['inventory'],
                 official={k:[dict(date=r['date'],close=r['close']) for r in rows] for k,rows in official['bars'].items()})
    (out/'data.js').write_text('window.INDEX_DATA='+json.dumps(payload,separators=(',',':')).replace('<','\\u003c')+';\n')
    shutil.copyfile(Path(__file__).with_name('explorer.html'),out/'index.html')
    assets=ROOT/'experiments/earnings_reaction_v1/er03_chart_assets'
    for source,target in [('lightweight-charts-5.0.9.js','lightweight-charts.js'),('LICENSE-lightweight-charts.txt','LICENSE-lightweight-charts.txt')]:shutil.copyfile(assets/source,out/target)
    return out/'index.html'


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('build',type=Path);a=p.parse_args();print(export(a.build))
