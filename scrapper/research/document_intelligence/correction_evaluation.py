"""Bounded offline correction comparison using the frozen Phase 5 scoring policy.

Original v1 references, results and extractor source stay immutable. Holdout
annotations must be frozen before predictions; never change them from predictions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from .evaluation import score, controls_score, context_for, original_text, compact, in_window, value_match, classification_match, period_key
from .extract import extract_intelligence
from .evidence import validate_evidence
from .offline import deny_network, extract_local
from .research_features import public_pit_readiness

CHANGED_BASELINE = {'scrapper/research/document_intelligence/'+name for name in ('extract.py','schema.py','evidence.py','numbers.py')}


def frozen_package(path, *, namespace='scrapper.research.phase5_frozen'):
    expected=json.loads((path/'SHA256SUMS.json').read_text())
    for name,digest in expected.items():
        if hashlib.sha256((path/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen source changed: '+name)
    name=namespace
    spec=importlib.util.spec_from_file_location(name,path/'__init__.py',submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return __import__(name+'.extract',fromlist=['extract_intelligence'])


def protection(baseline_root):
    repo=Path(__file__).resolve().parents[3]
    pinned=json.loads((baseline_root/'protected-and-baseline-source.json').read_text())
    changed=[name for name,digest in pinned.items() if hashlib.sha256((repo/name).read_bytes()).hexdigest()!=digest]
    disallowed=set(changed)-CHANGED_BASELINE
    if disallowed:
        raise ValueError('Protected operational/baseline source changed: '+str(sorted(disallowed)))
    return {'checked':len(pinned),'authorized_corrected_sources':changed,'protected_changes':sorted(disallowed)}


def input_checks(manifest_path,refs_path):
    manifest=json.loads(manifest_path.read_text());refs=json.loads(refs_path.read_text())
    if not 1<=len(manifest['documents'])<=12 or len(refs['references'])>150:
        raise ValueError('Offline small-sample bounds exceeded')
    if hashlib.sha256(refs_path.read_bytes()).hexdigest()!=refs_path.with_suffix('.sha256').read_text().strip():
        raise ValueError('Frozen references changed')
    for row in manifest['documents']:
        path=(manifest_path.parent/row['path']).resolve()
        if not path.is_relative_to(manifest_path.parent.resolve()) or path.stat().st_size>50*1024*1024:
            raise ValueError('Unsafe original')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['content_sha256']:
            raise ValueError('Original hash mismatch')
        for ref in [r for r in refs['references'] if r['document_key']==row['key']]:
            raw=original_text(body,path.suffix,ref['page'])
            if any(compact(t) not in compact(raw) for t in ref['required_source_terms']):
                raise ValueError('Independent original-byte source check failed: '+ref['reference_id'])
    return manifest,refs


def supplemental(by_doc,refs):
    """Assess time fields on exact numeric matches; don't rewrite v1 annotations."""
    windows={w['window_id']:w for w in refs['windows']};rows=[]
    for ref in refs['references']:
        if not ref.get('reporting_period'):
            continue
        matches=[r for r in by_doc[ref['document_key']] if in_window(r,windows[ref['window_id']]) and value_match(r,ref) and classification_match(r,ref)]
        asof=bool(__import__('re').fullmatch(r'\d{4}-\d{2}-\d{2}',ref['reporting_period']))
        actual=[getattr(r,'as_of_date',None) if asof else r.reporting_period for r in matches]
        rows.append({'reference_id':ref['reference_id'],'field':'as_of_date' if asof else 'reporting_period',
                     'expected':ref['reporting_period'],'actual':actual,
                     'correct':any(period_key(x)==period_key(ref['reporting_period']) for x in actual)})
    return {'correct':sum(x['correct'] for x in rows),'denominator':len(rows),'details':rows,
            'policy':'date-valued v1 reporting labels assessed as as_of_date; unchanged frozen score reported separately'}


def evaluate(manifest,refs,manifest_path,output,frozen,*,canonical_root=None):
    by={};documents=[];citations=[]
    output.mkdir()
    for row in manifest['documents']:
        source=manifest_path.parent/row['path']
        canonical=json.loads((canonical_root/(row['key']+'.canonical.json')).read_text()) if canonical_root else extract_local(source.read_bytes(),source.suffix)
        try:
            ctx=context_for(row) if frozen is None else frozen.DocumentContext(**{k:v for k,v in row.items() if k in frozen.DocumentContext.__dataclass_fields__})
            records,diag=(extract_intelligence if frozen is None else frozen.extract_intelligence)(ctx,canonical)
            failure=None
        except Exception as exc:
            records,diag=[],{}
            failure={'type':type(exc).__name__,'message':str(exc)}
        by[row['key']]=records
        for r in records:
            # Use the corrected validator for both sets; no validation relaxation.
            if r.status in {'VERIFIED','EVIDENCED_STATEMENT'}:
                citations.append({'record_id':r.record_id,'valid':validate_evidence(r,canonical),
                                 'annotated_window':any(in_window(r,w) for w in refs['windows'] if w['document_key']==row['key'])})
        (output/(row['key']+'.evidence.jsonl')).write_text(''.join(json.dumps(r.to_dict())+'\n' for r in records))
        (output/(row['key']+'.diagnostics.json')).write_text(json.dumps(diag,indent=2)+'\n')
        (output/(row['key']+'.canonical.json')).write_text(json.dumps(canonical,indent=2)+'\n')
        documents.append({'key':row['key'],'failed':failure,'records':len(records)})
    result=score(by,refs)
    result.update(documents=documents,document_failures={'failed':sum(bool(d['failed']) for d in documents),'denominator':len(documents)},
                  citation_validity={'valid':sum(c['valid'] for c in citations),'checked':len(citations),
                      'annotated_valid':sum(c['valid'] for c in citations if c['annotated_window']),
                      'annotated_checked':sum(c['annotated_window'] for c in citations)},
                  negative_controls=controls_score(by,refs),typed_time_fields=supplemental(by,refs),
                  source_labels='AGENT_SOURCE_CHECKED_HUMAN_ADJUDICATION_PENDING',
                  public_pit={'eligible':sum(public_pit_readiness(row,'2026-10-13T00:00:00Z')['eligible'] for row in manifest['documents']),
                              'denominator':len(documents)},
                  interpretation='Precision/recall apply to annotated windows and enumerated metrics only; no corpus-wide accuracy claim')
    (output/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def run(baseline_root,holdout_root,frozen_source,output):
    baseline_root,holdout_root,frozen_source,output=map(lambda p:Path(p).resolve(),[baseline_root,holdout_root,frozen_source,output])
    from ...config import STORE,DOCUMENT_STORE,EXPORTS,DB_PATH
    if output.exists() or output.is_relative_to(Path('/Volumes')) or any(output.is_relative_to(p.resolve()) for p in [STORE,DOCUMENT_STORE,EXPORTS,DB_PATH.parent]) or output.is_relative_to(holdout_root) or output.is_relative_to(baseline_root):
        raise ValueError('Use a fresh isolated output')
    guard=protection(baseline_root)
    original_manifest=baseline_root/'inputs/manifest.json';original_refs=baseline_root/'inputs/references.json'
    hold_manifest=holdout_root/'inputs/manifest.json';hold_refs=holdout_root/'inputs/references.json'
    bm,br=input_checks(original_manifest,original_refs);hm,hr=input_checks(hold_manifest,hold_refs)
    if {r['company_id'] for r in bm['documents']} & {r['company_id'] for r in hm['documents']}:
        raise ValueError('Holdout companies overlap development companies')
    frozen=frozen_package(frozen_source)
    output.mkdir(parents=True)
    results={}
    with deny_network():
        for name,manifest,refs,mp,canonical in [('development',bm,br,original_manifest,baseline_root/'results'),('holdout',hm,hr,hold_manifest,None)]:
            for revision in ['baseline','corrected']:
                results[name+'_'+revision]=evaluate(manifest,refs,mp,output/(name+'-'+revision),frozen if revision=='baseline' else None,canonical_root=canonical)
    # Make sure our rerun reproduces the frozen baseline, rather than changing
    # score policy to manufacture an improvement.
    frozen_result=json.loads((baseline_root/'results/evaluation.json').read_text())
    if any(results['development_baseline'][key]!=frozen_result[key] for key in ('categories','attributes')):
        raise ValueError('Frozen baseline scoring did not reproduce')
    for name,refs in [('development',br),('holdout',hr)]:
        old={x['reference_id']:x['outcome'] for x in results[name+'_baseline']['reference_results']}
        new={x['reference_id']:x['outcome'] for x in results[name+'_corrected']['reference_results']}
        results[name+'_transitions']={'improved':[r for r in old if old[r]!='TP' and new[r]=='TP'],
                                   'regressed':[r for r in old if old[r]=='TP' and new[r]!='TP']}
    input_checks(original_manifest,original_refs);input_checks(hold_manifest,hold_refs)
    results['protection_before']=guard;results['protection_after']=protection(baseline_root)
    results['generated_at']=datetime.now(timezone.utc).isoformat()
    results['safeguards']={'network':'denied','production_database_access':False,'schedule_changes':False,
                          'bulk_processing':False,'external_model_calls':0,'ml_admission':'BLOCKED'}
    results['source_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    (output/'comparison.json').write_text(json.dumps(results,indent=2)+'\n')
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--holdout',type=Path,required=True)
    p.add_argument('--frozen-source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();r=run(args.baseline,args.holdout,args.frozen_source,args.output)
    print(json.dumps({k:{'categories':v['categories'],'document_failures':v['document_failures']} for k,v in r.items() if isinstance(v,dict) and 'categories' in v},indent=2))

if __name__=='__main__':
    main()
