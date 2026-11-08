"""Three pinned revisions on frozen, source-first offline holdout annotations.

This is an evaluation harness, never a worker or a production admission path.
Each revision runs in a fresh process; canonical parsing and annotation review
are separate stages. No label or extractor changes follow holdout feedback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import time

from .correction_evaluation import evaluate, frozen_package, input_checks
from .offline import deny_network, extract_local


def hash_files(root):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}


def check_freeze(inputs):
    frozen = json.loads((inputs / 'freeze.json').read_text())
    for name, digest in frozen['files'].items():
        if hashlib.sha256((inputs / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen holdout changed: ' + name)
    return frozen


def run(inputs, output, *, source=None, canonical=None):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    frozen = check_freeze(inputs)
    manifest, refs = input_checks(inputs / 'manifest.json', inputs / 'references.json')
    review = json.loads((inputs.parent.parent / 'independent-review.json').read_text())
    if review.get('summary', {}).get('fresh_holdout_reviewed', 0) < 10 or not any(
            r['sha256'] == frozen['files']['references.json'] for r in review['methodology']['review_sources']):
        raise ValueError('Prediction-blind independent review must finish first')
    start, cpu = time.perf_counter(), time.process_time()
    extractor = frozen_package(Path(source), namespace='scrapper.research.phase5_comparison') if source else None
    with deny_network():
        if canonical is None:
            output.mkdir(parents=True, exist_ok=False)
            parsing = []
            for row in manifest['documents']:
                before = time.perf_counter()
                p = inputs / row['path']
                data = extract_local(p.read_bytes(), p.suffix)
                if len(data['pages']) > 100:
                    raise ValueError('100-page ceiling exceeded')
                (output / (row['key'] + '.canonical.json')).write_text(json.dumps(data, indent=2) + '\n')
                parsing.append({'key': row['key'], 'seconds': time.perf_counter()-before, 'pages': len(data['pages'])})
            result = {'canonical_parsing': parsing}
        else:
            result = evaluate(manifest, refs, inputs / 'manifest.json', output, extractor, canonical_root=Path(canonical))
            result['source_labels'] = 'SOURCE_FIRST_WITH_SEPARATE_AGENT_REVIEW_HUMAN_ADJUDICATION_PENDING'
        check_freeze(inputs)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result['measurements'] = {'wall_seconds': time.perf_counter()-start, 'cpu_seconds': time.process_time()-cpu,
                              'peak_process_rss_bytes': rss if platform.system() == 'Darwin' else rss*1024,
                              'external_api_cost_usd': 0, 'local_energy_cost': 'UNMEASURED'}
    result.update(generated_at=datetime.now(timezone.utc).isoformat(), annotation_sha256=frozen['files']['references.json'],
                  source_sha256=hash_files(Path(source) if source else Path(__file__).parent),
                  safeguards={'network': 'denied', 'database_connections': 0, 'production_writes': 0,
                              'scheduler_changes': 0, 'ml_admission': 'BLOCKED'})
    (output / 'evaluation.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--canonical', type=Path)
    args = parser.parse_args()
    result = run(args.inputs, args.output, source=args.source, canonical=args.canonical)
    print(json.dumps({k: result[k] for k in ('categories', 'attributes', 'measurements') if k in result}, indent=2))
