"""Reproducible offline build: python -m scrapper.indices INPUT.json OUTPUT_DIR."""
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from .service import daily, weekly, leave_one_out


def build(source, destination):
    raw = Path(source).read_bytes()
    data = json.loads(raw, parse_float=Decimal)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    datasets, coverage = {}, []
    for group in data['groups']:
        name, members = group['code'], group['members']
        if name in datasets:
            raise ValueError('Duplicate group')
        if len(set(members)) < 2:
            coverage.append(dict(code=name, status='UNAVAILABLE', reason='FEWER_THAN_TWO_VERIFIED_MEMBERS'))
            continue
        if group.get('membership_status') != 'VERIFIED_RETROSPECTIVE':
            coverage.append(dict(code=name, status='UNAVAILABLE', reason='MEMBERSHIP_NOT_VERIFIED'))
            continue
        rows = daily(data['prices'], data['calendar'], data['benchmark'], members=members,
                     actions=data.get('actions', []), listings=data.get('listings', {}))
        complete = all(r['index_close'] is not None for r in rows)
        datasets[name] = dict(members=members, evidence=group, daily=rows,
                              weekly=weekly(rows, data['calendar'], completed_through=data['calendar'][-1]),
                              leave_one_out=leave_one_out(data['prices'], data['calendar'], data['benchmark'], members=members,
                                                         actions=data.get('actions', []), listings=data.get('listings', {})))
        coverage.append(dict(code=name, status='COMPLETED' if complete else 'PARTIAL',
                             valid_levels=sum(r['index_close'] is not None for r in rows),
                             observations=len(rows), intraday_status='UNAVAILABLE_NOT_BUILT'))
    output = dict(source_sha256=hashlib.sha256(raw).hexdigest(), datasets=datasets,
                  coverage=coverage, benchmark=data['benchmark'],
                  limitations='Retrospective membership; corporate action certification is input-dependent. No intraday output.')
    (destination/'indices.json').write_text(json.dumps(output, default=str, indent=2)+'\n')
    # JS asset permits a local file:// chart without an HTTP server.
    (destination/'data.js').write_text('window.INDEX_DATA='+json.dumps(output, default=str).replace('<', '\\u003c')+';\n')
    assets = Path(__file__).resolve().parents[2]/'experiments/earnings_reaction_v1/er03_chart_assets'
    shutil.copyfile(assets/'lightweight-charts-5.0.9.js', destination/'lightweight-charts.js')
    shutil.copyfile(assets/'LICENSE-lightweight-charts.txt', destination/'LICENSE-lightweight-charts.txt')
    shutil.copyfile(Path(__file__).with_name('viewer.html'), destination/'index.html')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = build(args.input, args.output)
    print(json.dumps(result['coverage']))


if __name__ == '__main__':
    main()
