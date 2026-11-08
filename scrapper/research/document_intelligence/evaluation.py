"""Bounded source-first evaluation and stage-isolated local benchmarks.

The scored corpus is frozen, manually entered references, not our predictions.
Percentages apply only to annotated windows. No scheduler, database or source
client is invoked. Controlled temporal contracts are reported separately.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import re
import resource
import statistics
import subprocess
import sys
import time
import unicodedata

from .schema import DocumentContext, ResearchEvidence
from .extract import extract_intelligence
from .evidence import validate_evidence
from .offline import deny_network, extract_local


def compact(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return re.sub(r"\s+", "", text).lower().replace("’", "'").replace("–", "-")


def original_text(body, suffix, page):
    """Independent source reader for annotation checks, not intelligence output."""
    if suffix == ".pdf":
        import io
        from pypdf import PdfReader
        return PdfReader(io.BytesIO(body)).pages[page-1].extract_text()
    if suffix == ".html":
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "html.parser")
        for node in soup.find_all(["script", "style", "noscript", "template"]):
            node.decompose()
        return soup.get_text(" ", strip=True)
    raise ValueError("Corporate reference scoring supports original PDF/HTML; format fixtures are separate")


def context_for(row):
    keys = {f.name for f in fields(DocumentContext)}
    return DocumentContext(**{k: v for k,v in row.items() if k in keys})


def in_window(record, window):
    region = compact(window["source_quote"])
    for ev in record.evidence:
        if ev.get("page") != window["page"] or not ev.get("quote"):
            continue
        quote = compact(ev["quote"])
        if len(quote) >= 12 and (quote in region or region in quote):
            return True
    return False


def same_value(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return all(k in left and same_value(left[k],v) for k,v in right.items())
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return left == right


def value_match(record, reference):
    if record.metric != reference["metric"]:
        return False
    if reference["normalized_value"] is None:
        quote=compact(' '.join(e.get('quote') or '' for e in record.evidence))
        return all(compact(term) in quote for term in reference['required_source_terms'])
    if record.status != "VERIFIED" or not same_value(record.normalized_value, reference["normalized_value"]):
        return False
    if reference["metric"] != "rating_action":
        return record.unit == reference["unit"] and record.currency == reference["currency"]
    return True


def classification_match(record, reference):
    return record.statement_kind == reference["statement_kind"] and record.value_basis == reference["value_basis"]


def period_key(value):
    if value is None:
        return None
    text = compact(value).replace("'", "")
    month_names = {m.lower():i for i,m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"],1)}
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?(?:of)?([a-z]+)(\d{4})",text)
    if match and match[2] in month_names:
        return f"{match[3]}-{month_names[match[2]]:02}-{int(match[1]):02}"
    return text


def _counts(tp, fp, fn):
    return {"tp":tp,"fp":fp,"fn":fn,"predictions":tp+fp,"references":tp+fn,
            "precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None}


def score(records_by_doc, reference_data):
    windows = {w["window_id"]:w for w in reference_data["windows"]}
    refs_by_window = defaultdict(list)
    for ref in reference_data["references"]:
        refs_by_window[ref["window_id"]].append(ref)
    categories = defaultdict(lambda: Counter(tp=0,fp=0,fn=0))
    details, extractions, field_results, errors = [], [], [], Counter()
    # Window precision includes only schema claims whose metrics were exhaustively
    # annotated in that window. Unscored pages/metrics are explicitly excluded.
    for wid, references in refs_by_window.items():
        win = windows[wid]
        candidates = [r for r in records_by_doc[win["document_key"]] if r.metric in win["score_metrics"] and in_window(r,win)]
        numeric_metrics = {ref["metric"] for ref in references if ref["normalized_value"] is not None}
        claims = [r for r in candidates if r.status == "VERIFIED" or (r.status == "EVIDENCED_STATEMENT" and
            any(ref['normalized_value'] is None and value_match(r,ref) for ref in references))]
        used = set()
        for ref in references:
            compatible = [r for r in claims if value_match(r,ref)]
            correct = [r for r in compatible if classification_match(r,ref) and r.record_id not in used]
            found = correct[0] if correct else None
            category = ref["category"]
            if found:
                categories[category]["tp"] += 1
                used.add(found.record_id)
                details.append({"reference_id":ref["reference_id"],"outcome":"TP","record_id":found.record_id,"category":category})
            else:
                categories[category]["fn"] += 1
                reason = "wrong_statement_type_or_value_basis" if compatible else "missing_verified_numeric_or_structured_disclosure"
                errors[reason] += 1
                details.append({"reference_id":ref["reference_id"],"outcome":"FN","record_id":None,"category":category,"reason":reason,
                                "candidate_statuses":dict(Counter(r.status for r in candidates if r.metric==ref["metric"]))})
            # Attribute diagnostics are assessed independently of core semantic
            # acceptance, preventing an incorrect basis from hiding attribution.
            counterpart = found or next((r for r in compatible if r.record_id not in used),None)
            if ref["normalized_value"] is not None:
                field_results.append({"reference_id":ref["reference_id"],"attribute":"actual_vs_projected",
                    "expected":ref["value_basis"],"actual":counterpart.value_basis if counterpart else None,
                    "result":"correct" if counterpart and counterpart.value_basis==ref["value_basis"] else "incorrect" if counterpart else "missing"})
            if ref["reporting_period"] is not None or ref["metric"]=="growth_guidance":
                expected=ref["reporting_period"]
                field_results.append({"reference_id":ref["reference_id"],"attribute":"reporting_period",
                    "expected":expected,"actual":counterpart.reporting_period if counterpart else None,
                    "result":"correct" if counterpart and period_key(counterpart.reporting_period)==period_key(expected) else "missing" if not counterpart or counterpart.reporting_period is None else "incorrect"})
            if ref["speaker_role"] is not None:
                attributed = counterpart or next((r for r in candidates if r.metric==ref["metric"] and r.status in {"EVIDENCED_STATEMENT","AMBIGUOUS"}),None)
                actor = attributed.speaker if attributed else None
                good = actor is not None and actor.get("role")==ref["speaker_role"] and compact(actor.get("name"))==compact(ref["speaker_name"])
                field_results.append({"reference_id":ref["reference_id"],"attribute":"speaker_attribution",
                    "expected":{"name":ref["speaker_name"],"role":ref["speaker_role"]},"actual":actor,
                    "result":"correct" if good else "missing" if actor is None or actor.get("role")=="UNKNOWN" else "incorrect"})
            if ref["entity_scope"] is not None:
                field_results.append({"reference_id":ref["reference_id"],"attribute":"entity_scope",
                    "expected":ref["entity_scope"],"actual":counterpart.dimensions if counterpart else None,
                    "result":"correct" if counterpart and counterpart.dimensions.get("entity_scope")==ref["entity_scope"] else "missing"})
            if ref["time_horizon"] is not None:
                field_results.append({"reference_id":ref["reference_id"],"attribute":"time_horizon",
                    "expected":ref["time_horizon"],"actual":counterpart.time_horizon if counterpart else None,
                    "result":"correct" if counterpart and period_key(counterpart.time_horizon)==period_key(ref["time_horizon"]) else "missing" if not counterpart or counterpart.time_horizon is None else "incorrect"})
        for claim in claims:
            if claim.record_id in used:
                continue
            relevant = [r for r in references if r["metric"]==claim.metric]
            if not relevant:
                # Metrics not enumerated by gold in this window are not silently
                # labeled wrong or right. They remain an explicit coverage gap.
                extractions.append({"record_id":claim.record_id,"window_id":wid,"outcome":"UNADJUDICATED_METRIC"})
                continue
            category=relevant[0]["category"]
            matches = [r for r in relevant if value_match(claim,r)]
            if matches and all(classification_match(claim,r) for r in matches):
                reason="duplicate_emission_or_unresolved_scope"
            elif matches:
                reason="incomplete_statement_typing" if claim.statement_kind=='UNKNOWN' else (
                    "incomplete_value_basis" if claim.value_basis=='UNKNOWN' and matches[0]['value_basis']!='UNKNOWN' and claim.statement_kind==matches[0]['statement_kind'] else "incorrect_actual_projection_or_statement_type")
            else:
                reason="incorrect_value_unit_metric_or_event_binding"
            categories[category]["fp"]+=1;errors[reason]+=1
            extractions.append({"record_id":claim.record_id,"window_id":wid,"outcome":"FP","category":category,
                                "reason":reason,"claim":claim.to_dict()})
    attributes={}
    for name in {r["attribute"] for r in field_results}:
        counts=Counter(r["result"] for r in field_results if r["attribute"]==name)
        correct,incorrect,missing=(counts[k] for k in ("correct","incorrect","missing"))
        attributes[name]={**_counts(correct,incorrect,missing+incorrect),"correct":correct,"incorrect":incorrect,"missing":missing,
                          "reference_denominator":correct+incorrect+missing,
                          "accuracy_on_emitted":correct/(correct+incorrect) if correct+incorrect else None,
                          "complete_and_correct":correct/(correct+incorrect+missing) if correct+incorrect+missing else None}
    return {"categories":{name:_counts(c["tp"],c["fp"],c["fn"]) for name,c in sorted(categories.items())},
            "attributes":attributes,"errors":dict(errors),"reference_results":details,
            "prediction_results":extractions,"field_results":field_results}


def controls_score(records_by_doc, reference_data):
    windows={w["window_id"]:w for w in reference_data["windows"]}
    rows=[]
    for control in reference_data["negative_controls"]:
        w=windows[control["window_id"]]
        candidates=[r for r in records_by_doc[w["document_key"]] if in_window(r,w)]
        name=control["forbidden"]
        if name=="guidance_withdrawal":
            bad=[r for r in candidates if "explicit_guidance_withdrawal" in r.reasons]
        elif name=="verified_management_revenue_500cr":
            bad=[r for r in candidates if r.metric=="revenue" and r.status=="VERIFIED" and r.normalized_value=="5000000000" and (r.speaker or {}).get("role")=="management"]
        elif name=="actual_leverage_0.5x":
            bad=[r for r in candidates if r.metric=="leverage" and r.status=="VERIFIED" and r.value_basis=="ACTUAL" and r.normalized_value=="0.5"]
        elif name=="working_capital_pressure":
            bad=[r for r in candidates if r.metric=="working_capital" and r.statement_kind=="RISK_WARNING" and r.status=="EVIDENCED_STATEMENT"]
        elif name=="new_rating_action":
            bad=[r for r in candidates if r.metric=="rating_action" and r.status=="VERIFIED"]
        elif name=="book_includes_l1_275cr":
            bad=[r for r in candidates if r.metric=="order_book" and r.status=="VERIFIED" and same_value(r.normalized_value,"2750000000")]
        else:
            raise ValueError(name)
        rows.append({**control,"passed":not bad,"bad_record_ids":[r.record_id for r in bad]})
    return rows


def benchmark_worker(manifest_path, key, stage, output, canonical_path=None, repeats=3, ocr=False):
    row=next(r for r in json.loads(Path(manifest_path).read_text())["documents"] if r["key"]==key)
    root=Path(manifest_path).resolve().parent
    source=(root/row["path"]).resolve()
    if not source.is_relative_to(root) or repeats not in {1,2,3}:
        raise ValueError("Invalid bounded worker input")
    body=source.read_bytes()
    if hashlib.sha256(body).hexdigest()!=row["content_sha256"]:
        raise ValueError("Source hash mismatch")
    runs=[]
    processing_error=None
    records=[]
    canonical={'pages':[],'extraction_status':'failed'}
    diagnostics={}
    with deny_network():
        if stage=="intelligence":
            canonical=json.loads(Path(canonical_path).read_text())
        for index in range(repeats):
            wall,cpu=time.perf_counter(),time.process_time()
            try:
                if stage=="extraction":
                    canonical=extract_local(body,source.suffix.lower(),ocr=ocr)
                else:
                    records,diagnostics=extract_intelligence(context_for(row),canonical)
            except Exception as exc:
                processing_error={"type":type(exc).__name__,"message":str(exc),"stage":stage}
                diagnostics={"processing_error":processing_error,"returned_records":0}
            runs.append({"repeat":index+1,"wall_seconds":time.perf_counter()-wall,"cpu_seconds":time.process_time()-cpu})
            if processing_error:
                break
    output=Path(output)
    if stage=="extraction":
        (output/(key+'.canonical.json')).write_text(json.dumps(canonical,indent=2)+'\n')
    else:
        (output/(key+'.evidence.jsonl')).write_text(''.join(json.dumps(r.to_dict())+'\n' for r in records))
        (output/(key+'.diagnostics.json')).write_text(json.dumps(diagnostics,indent=2)+'\n')
    usage=resource.getrusage(resource.RUSAGE_SELF)
    report={"key":key,"stage":stage,"source_bytes":len(body),"pages":len(canonical['pages']),
            "format":source.suffix,"extraction_status":canonical['extraction_status'],
            "measurements":runs,"cold_wall_seconds":runs[0]['wall_seconds'],
            "warm_median_wall_seconds":statistics.median(r['wall_seconds'] for r in runs[1:]) if len(runs)>1 else None,
            "peak_process_rss_bytes":usage.ru_maxrss if platform.system()=='Darwin' else usage.ru_maxrss*1024,
            "machine":platform.machine(),"python":platform.python_version(),"interpretation_model_calls":0,"paid_external_model_cost":0,
            "ocr":"local_tesseract" if ocr else "disabled","ocr_page_count":canonical.get('ocr_page_count',0),
            "outcome":"FAILED" if processing_error else "COMPLETED","processing_error":processing_error,
            "rss_basis":"fresh child high-water mark includes imports and loaded source; not incremental stage allocation"}
    (output/(key+'.'+stage+'.benchmark.json')).write_text(json.dumps(report,indent=2)+'\n')


def run(manifest_path, references_path, output, *, repeats=3, format_manifest=None):
    manifest_path,references_path,output=Path(manifest_path).resolve(),Path(references_path).resolve(),Path(output).resolve()
    if output.exists() or output.is_relative_to(manifest_path.parent) or output.is_relative_to(Path('/Volumes')):
        raise ValueError("Use a fresh isolated output directory")
    from ...config import STORE,DOCUMENT_STORE,EXPORTS,DB_PATH
    if any(output.is_relative_to(p.resolve()) for p in [STORE,DOCUMENT_STORE,EXPORTS,DB_PATH.parent]):
        raise ValueError("Production output is forbidden")
    manifest=json.loads(manifest_path.read_text())
    refs=json.loads(references_path.read_text())
    baseline_file=references_path.parent.parent/'protected-and-baseline-source.json'
    protected=json.loads(baseline_file.read_text())
    repository=Path(__file__).resolve().parents[3]
    changed=[name for name,digest in protected.items() if hashlib.sha256((repository/name).read_bytes()).hexdigest()!=digest]
    if changed:
        raise ValueError('Frozen baseline or protected source changed: '+json.dumps(changed))
    if not 1<=len(manifest['documents'])<=12 or len(refs['references'])>150 or repeats not in {1,2,3}:
        raise ValueError("Evaluation exceeds explicit small-sample bounds")
    expected=references_path.with_suffix('.sha256').read_text().strip()
    if hashlib.sha256(references_path.read_bytes()).hexdigest()!=expected:
        raise ValueError("Reference annotations changed after freeze")
    source_checks=[]
    root=manifest_path.parent
    for row in manifest['documents']:
        source=(root/row['path']).resolve()
        if not source.is_relative_to(root) or source.stat().st_size>50*1024*1024:
            raise ValueError("Unsafe input")
        body=source.read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['content_sha256']:
            raise ValueError("Original hash mismatch")
        relevant=[r for r in refs['references'] if r['document_key']==row['key']]
        for page in {r['page'] for r in relevant}:
            text=original_text(body,source.suffix,page)
            for ref in [r for r in relevant if r['page']==page]:
                missing=[term for term in ref['required_source_terms'] if compact(term) not in compact(text)]
                source_checks.append({'reference_id':ref['reference_id'],'page':page,'missing_terms':missing,
                                      'source_sha256':row['content_sha256'],'reader':'independent_pypdf_or_BeautifulSoup'})
    if any(r['missing_terms'] for r in source_checks):
        raise ValueError('Independent source checks failed: '+json.dumps([r for r in source_checks if r['missing_terms']]))
    output.mkdir(parents=True,exist_ok=False)
    (output/'source-annotation-checks.json').write_text(json.dumps(source_checks,indent=2)+'\n')
    benchmark=[]
    for row in manifest['documents']:
        for stage in ['extraction','intelligence']:
            command=[sys.executable,'-m','scrapper.research.document_intelligence.evaluation','worker',
                     '--manifest',str(manifest_path),'--key',row['key'],'--stage',stage,'--output',str(output),'--repeats',str(repeats)]
            if stage=='intelligence':
                command+=['--canonical',str(output/(row['key']+'.canonical.json'))]
            subprocess.run(command,check=True,timeout=60)
            benchmark.append(json.loads((output/(row['key']+'.'+stage+'.benchmark.json')).read_text()))
    by_doc={row['key']:[ResearchEvidence(**json.loads(line)) for line in (output/(row['key']+'.evidence.jsonl')).read_text().splitlines()] for row in manifest['documents']}
    result=score(by_doc,refs)
    citation_checks=[]
    for row in manifest['documents']:
        canonical=json.loads((output/(row['key']+'.canonical.json')).read_text())
        wins=[w for w in refs['windows'] if w['document_key']==row['key']]
        for record in by_doc[row['key']]:
            if record.status not in {'VERIFIED','EVIDENCED_STATEMENT'} or not any(in_window(record,w) for w in wins):
                continue
            citation_checks.append({'record_id':record.record_id,'exact_citation_valid':validate_evidence(record,canonical)})
    result.update(schema_version='phase5-evaluation-v1',generated_at=datetime.now(timezone.utc).isoformat(),
        reference_sha256=expected,annotation_method=refs['methodology'],
        counts={'documents':len(manifest['documents']),'companies':len({r['company_id'] for r in manifest['documents']}),
                'source_pages':sum(r['pages'] for r in manifest['documents']),'windows':len(refs['windows']),'references':len(refs['references']),
                'all_emitted_records':sum(len(r) for r in by_doc.values())},
        evidence={'checked':len(citation_checks),'valid':sum(r['exact_citation_valid'] for r in citation_checks),
                  'method':'exact canonical span/cell checks; semantic entailment is assessed separately',
                  'checks':citation_checks},negative_controls=controls_score(by_doc,refs),
        benchmark=benchmark,
        public_pit={'documents_with_publication_timestamp':sum(r.get('publication_at') is not None for r in manifest['documents']),
                    'certified_for_historical_backtest':0,'reason':'precise publicly available version timestamps and public provenance are not established for these archives'},
        safeguards={'network':'denied in stage workers','database_connections':0,'model_calls':0,'production_changes':0,'bulk_processing':False})
    from .research_features import build_examples
    examples=build_examples(manifest,refs,decision_at='2026-10-13T00:00:00Z')
    (output/'research-feature-examples.json').write_text(json.dumps(examples,indent=2)+'\n')
    result['feature_examples']={'count':len(examples),'with_values':sum(not f['missing'] for f in examples),
                                'public_pit_eligible':sum(f['public_pit_eligible'] for f in examples),
                                'basis':'source-checked reference inputs; not automated baseline extractor success; human adjudication pending'}
    if format_manifest:
        format_manifest=Path(format_manifest).resolve()
        format_rows=json.loads(format_manifest.read_text())['documents']
        if len(format_rows)>3:
            raise ValueError('Format benchmark is bounded to three existing fixture variants')
        format_output=output/'format-benchmarks';format_output.mkdir()
        result['format_benchmarks']=[]
        for row in format_rows:
            for stage in ['extraction','intelligence']:
                command=[sys.executable,'-m','scrapper.research.document_intelligence.evaluation','worker',
                         '--manifest',str(format_manifest),'--key',row['key'],'--stage',stage,'--output',str(format_output),'--repeats',str(repeats)]
                if row['key']=='scan_local_ocr':
                    command+=['--ocr']
                if stage=='intelligence':
                    command+=['--canonical',str(format_output/(row['key']+'.canonical.json'))]
                subprocess.run(command,check=True,timeout=60)
                result['format_benchmarks'].append(json.loads((format_output/(row['key']+'.'+stage+'.benchmark.json')).read_text()))
    result['source_guard']={'checked':len(protected),'changed':[name for name,digest in protected.items() if hashlib.sha256((repository/name).read_bytes()).hexdigest()!=digest]}
    result['evaluator_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for row in manifest['documents']:
        if hashlib.sha256((root/row['path']).read_bytes()).hexdigest()!=row['content_sha256']:
            raise ValueError('Original changed during evaluation')
    (output/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['run','worker'])
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--references',type=Path)
    p.add_argument('--key')
    p.add_argument('--stage',choices=['extraction','intelligence'])
    p.add_argument('--canonical',type=Path)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--format-fixtures',type=Path,help='Separate bounded existing PPTX/scanned fixture benchmark, excluded from corporate accuracy')
    p.add_argument('--ocr',action='store_true',help='Installed local OCR for format benchmarks only')
    if (args:=p.parse_args()).mode=='worker':
        benchmark_worker(args.manifest,args.key,args.stage,args.output,args.canonical,args.repeats,ocr=args.ocr)
    else:
        if not args.references:
            p.error('--references is required for scoring')
        result=run(args.manifest,args.references,args.output,repeats=args.repeats,format_manifest=args.format_fixtures)
        print(json.dumps({'output':str(args.output),'counts':result['counts'],'categories':result['categories'],'attributes':result['attributes']},indent=2))


if __name__=='__main__':
    main()
