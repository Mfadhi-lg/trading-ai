"""Collect actual development runs into the requested four-company output layout."""
import argparse
import json
from pathlib import Path
import shutil
from .provider import digest,save
from .runner import render


def summarize(root):
    root=Path(root);manifest=json.loads((root/'pilot_manifest.json').read_text())
    offline=root/'offline-development';smoke=root/'vimta-smoke'
    usage_path=root/'inference/inference_usage.json'
    usage=json.loads(usage_path.read_text()) if usage_path.exists() else dict(requests=[])
    calls=usage['requests'];results=[]
    for c in manifest['companies']:
        replay=root/'replayed-final'
        source=(replay if (replay/c['company_id']/'evidence.json').exists() else
            smoke if c['company_id']=='VIMTALABS' and (smoke/c['company_id']/'evidence.json').exists() else offline)/c['company_id']
        dest=root/c['company_id'];dest.mkdir(exist_ok=True)
        for name in ('evidence.json','coverage.json','unresolved.json','evidence_report.html','human_review_template.json'):
            shutil.copyfile(source/name,dest/name)
        rows=json.loads((dest/'evidence.json').read_text());coverage=json.loads((dest/'coverage.json').read_text())
        render(c,rows,json.loads((dest/'unresolved.json').read_text()),dest/'evidence_report.html')
        for d in c['documents']:
            ds=[r for r in rows if r['document_id']==d['document_id']]
            requests=[r for r in calls if r['packet']['document_id']==d['document_id']]
            cov=next(x for x in coverage if x['document_id']==d['document_id'])
            results.append(dict(company_id=c['company_id'],document_id=d['document_id'],document_type=d['document_type'],
                split=c['split'],physical_pages=d['physical_pages'],native_pages_processed=sum(p.get('native_processed',False) for p in cov['pages']),
                native_text_pages=sum(p.get('native_text_available',p.get('text_characters',0)>0) for p in cov['pages']),
                tables_detected=sum(p.get('tables_detected',0) for p in cov['pages']) if any(p.get('native_processed') for p in cov['pages']) else None,
                charts_detected=None,charts_processed=0,review_candidates=len(ds),accepted_observations=sum(r['validation_status']=='accepted' for r in ds),
                api_candidates=sum(r['extraction_method'].startswith('API_') for r in ds),
                source_matched_candidates=sum(r['anchor_verified'] for r in ds),unresolved_disclosures=sum(r['validation_status']!='accepted' for r in ds),
                fully_correct_financial_observations=None,incorrect_values=None,incorrect_periods_units_scope=None,
                incorrect_source_attribution=None,unsupported_accepted_claims=None,material_omissions=None,correct_source_references=None,
                independently_reviewed_accepted_observations=0,material_recall=None,
                parser_seconds=cov.get('elapsed_seconds'),inference_seconds=sum(r.get('latency_seconds') or 0 for r in requests),
                input_tokens=sum(r.get('input_tokens') or 0 for r in requests),output_tokens=sum(r.get('output_tokens') or 0 for r in requests),
                api_cost_usd=sum(r.get('cost_usd') or 0 for r in requests),
                failed_requests=sum(r['status'] in ('failed_or_truncated','interrupted_unknown_billing') for r in requests),
                offline_recovered_requests=sum(r['status']=='completed_after_offline_envelope_recovery' for r in requests),
                cost_status='provider_reported' if requests and all(r.get('cost_usd') is not None for r in requests) else 'unknown' if requests else 'no_calls'))
    usage.update(total_calls=len(calls),total_input_tokens=sum(r.get('input_tokens') or 0 for r in calls),
        total_output_tokens=sum(r.get('output_tokens') or 0 for r in calls),
        total_provider_reported_cost_usd=sum(r.get('cost_usd') or 0 for r in calls),
        total_charged_or_reserved_usd=sum(r['charged_or_reserved_usd'] for r in calls),
        approved_budget_usd=.10,approved_max_calls=6,
        unknown_cost_requests=sum(r.get('cost_usd') is None for r in calls),
        note='Provider-reported cost is separate from conservative spending reservations. Missing usage is not zero usage.')
    save(root/'inference_usage.json',usage)
    summary=dict(schema_version='unified-pilot/0.1',selection_companies=4,source_documents=12,
        status='INCOMPLETE_INDEPENDENT_REVIEW_REQUIRED',ready_for_evaluation=False,
        acceptance_targets=dict(factual_correctness=.95,material_recall=.90,critical_unsupported_accepted=0,source_anchoring=1),
        acceptance_achieved=None,per_document=results,
        evaluation_company_ids=['IDFCFIRSTB','RECLTD'],
        independent_review_status='No independent reference annotations; no factual correctness or recall claim',
        inference_usage_file='inference_usage.json',
        next_action='Review Vimta smoke output against original pages; resolve demonstrated failures before approving second-development-company inference')
    save(root/'evaluation_summary.json',summary)
    template=smoke/'reference_labels_template.json' if (smoke/'reference_labels_template.json').exists() else offline/'reference_labels_template.json'
    shutil.copyfile(template,root/'reference_labels_template.json')
    instructions='''# Unified financial document intelligence pilot V0.1

Scope revised by user to four companies and 12 PDFs. Membership comes from the two sample_files-2 manifests. Development: Vimta Labs and Everest Kanto Cylinder. Frozen evaluation: IDFC First Bank and REC. No source PDF is missing. Evaluation is not genuinely unseen: prior hash references are retained per document.

The shared contract, D03 extraction, offline review, focused API adapter, request caching, budget ledger and source-linked dossiers are implemented in experiments/unified_financial_pilot. Production modules and V1/V2 experiments were not edited. No RunPod requests were made.

All source windows and API disclosures remain needs_review until independent source adjudication. Quote matches do not prove financial semantics. Independent material-disclosure reference annotations are missing; factual correctness, material recall and acceptance remain unmeasured. All chart numerical relationships and cross-page transcript corrections require review.

'''
    instructions+=f"Processed development pages: {sum(r['native_pages_processed'] for r in results)}; pages with native text: {sum(r['native_text_pages'] for r in results)}. Native table regions: {sum(r['tables_detected'] or 0 for r in results)}. Review candidates: {sum(r['review_candidates'] for r in results)}. API calls: {len(calls)}. Provider-reported cost: ${usage['total_provider_reported_cost_usd']:.8f}; unknown-cost requests: {usage['unknown_cost_requests']}. Conservative reserved spending: ${usage['total_charged_or_reserved_usd']:.8f}.\n\n"
    instructions+='Evaluation sources are cover-inspected and hash-checked only; their extraction reports explicitly show unprocessed status. Do not claim all-document completion. The next action is independent review of the Vimta smoke, then a separately budgeted EKC development run; freeze the tested prompts and configuration before seeking evaluation approval.\n\nObserved smoke failures: one interrupted long-running request with unknown billing, one truncated transcript response, and one fenced-JSON response recovered offline without changing its content. JSON-number values were retained as strings with formatting-review flags; exact table quotes and speaker/period binding remain subject to review. A hard wall deadline and bounded four-disclosure prompt were added after those failures. All six authorized API calls are consumed; no additional paid inference is permitted under this approval.\n'
    api_total=sum(r['api_candidates'] for r in results)
    all_rows=[r for c in manifest['companies'] for r in json.loads((root/c['company_id']/'evidence.json').read_text())]
    api_rows=[r for r in all_rows if r['extraction_method'].startswith('API_')]
    api_anchors=sum(r['anchor_verified'] for r in api_rows)
    instructions+=f'\nAPI extraction yielded {api_total} structured candidates: {api_anchors} literal original-page matches and {api_total-api_anchors} quote/page mismatches. These are anchoring outcomes, not factual correctness labels. No observations are admitted. The four recovered transcript candidates all fail the strict contiguous original-text anchor and require review; no fuzzy automatic admission was added.\n'
    instructions+='\nKnown gaps: the rating page-2 qualitative request did not complete, leaving detailed liquidity and historical financial analysis outside successful API extraction. Only presentation pages 8 and 25 received model requests; chart page 7 was rendered and spot-inspected, but its chart series were not model-extracted. The other presentation charts, source pages and transcript sections remain outside semantic coverage. No material recall denominator exists. Document dates span 2023–2026 and must not be presented as one quarter.\n\nValidation: 20 pilot unit/integration tests passed after fixes, and 22 unchanged D03/rating/transcript regression tests passed. The initial numeric substring test failed before its boundary fix; the failure is documented as development history, not a final passing test. The slow-body deadline test reproduces the timeout issue without network or payment.\n\nThe first offline run processed 87 pages in 3.26 seconds; the final offline replay took about 3.08 seconds. These are small-sample parser timings, separate from API latency. Per-request times, including the 158-second interrupted request, remain in inference_usage.json. Total billing and token usage remain incomplete because that interrupted request returned no usage.\n'
    instructions+='\nSelected documents (full paths, hashes, page counts, dates and cover citations are in pilot_manifest.json):\n\n'
    for c in manifest['companies']:
        instructions+='- '+c['company_name']+' ('+c['company_id']+', '+c['split']+'): '+ '; '.join(d['document_type']+' — '+str(d['document_date'] or 'date unknown')+', '+str(d['physical_pages'])+' physical pages, '+str(d['reporting_period']) for d in c['documents'])+'.\n'
    (root/'evaluation_summary.md').write_text(instructions)
    # Pin implementation for audit; this records bytes, not a declaration that
    # development is complete or evaluation is authorized.
    code_root=Path(__file__).parent
    save(root/'implementation_snapshot.json',dict(status='development_snapshot_not_evaluation_freeze',
        files={str(p.relative_to(code_root)):digest(p.read_bytes()) for p in sorted(code_root.glob('*')) if p.is_file()}))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='artifacts/unified-financial-pilot-v0.1')
    a=p.parse_args();s=summarize(a.root);print(s['status'])
