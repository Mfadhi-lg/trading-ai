"""Independent review intake and per-document correctness/recall metrics.

Reference labels must be authored from originals, separately from extraction.
This module never creates reference facts or replaces missing labels with zero.
"""
import argparse
import json
from pathlib import Path
from .provider import save
from .schema import Evidence,validate,review_checks
from .runner import render,check_selection

CHECKS=('value','unit','period','scope','column_association','attribution','qualifications','rating_action_status')


def reviewed(annotation, document_type=None):
    return (annotation.get('reviewer') and annotation.get('reviewed_at') and annotation.get('independent') is True
        and all(annotation.get(k) in ('correct','incorrect','not_applicable') for k in review_checks(document_type))
        and (document_type != 'presentation' or all(annotation.get(k) in ('correct','incorrect')
             for k in ('business_association','source_region')))
        and type(annotation.get('critical_unsupported')) is bool
        and type(annotation.get('correct_source_reference')) is bool)


def evaluate(manifest_path,run_path,reviews_path,references_path,output):
    manifest=check_selection(manifest_path);run_path=Path(run_path);output=Path(output)
    safe=Path(__file__).resolve().parents[2]/'artifacts/unified-financial-pilot-v0.1'
    if not output.resolve().is_relative_to(safe) or output.exists():raise ValueError('Use new isolated evaluation directory')
    annotations=json.loads(Path(reviews_path).read_text())['observations']
    reference_docs=json.loads(Path(references_path).read_text())['document_reviews']
    if len({a['evidence_id'] for a in annotations})!=len(annotations):raise ValueError('Duplicate review IDs')
    reviews={a['evidence_id']:a for a in annotations}
    if len({d['document_id'] for d in reference_docs})!=len(reference_docs):raise ValueError('Duplicate reference documents')
    references={d['document_id']:d for d in reference_docs}
    output.mkdir(parents=True);results=[];known_ids=set()
    for company in manifest['companies']:
        dest=output/company['company_id'];dest.mkdir();rows=json.loads((run_path/company['company_id']/'evidence.json').read_text())
        known_ids.update(r['evidence_id'] for r in rows)
        canonical={}
        for d in company['documents']:
            if not any(r['document_id']==d['document_id'] for r in rows):continue
            # Re-open original bytes; a cached matching quote is not sufficient.
            from .provider import digest
            from scrapper.research.document_intelligence.offline import extract_local,deny_network
            body=Path(d['source_path']).read_bytes()
            if digest(body)!=d['sha256']:raise ValueError('Source changed before human admission')
            with deny_network():canonical[d['document_id']]=extract_local(body,'.pdf',ocr=False)
        for row in rows:
            annotation=reviews.get(row['evidence_id'])
            if not annotation or not reviewed(annotation,row['document_type']):continue
            e=Evidence(**{k:v for k,v in row.items() if k!='evidence_id'})
            e.review=annotation
            pages={p['page_or_slide_number']:p['selected_text'] for p in canonical[row['document_id']]['pages']}
            context=None
            if row['document_type']=='presentation':
                from .runner import make_packet
                doc=next(d for d in company['documents'] if d['document_id']==row['document_id'])
                packet,_=make_packet(doc,canonical[row['document_id']],'qualitative',row['physical_page'])
                context=packet['representation']['pages'][0]
            e=validate(e,pages,row['source_sha256'],region_context=context)
            if annotation['critical_unsupported'] or not annotation['correct_source_reference']:
                e.validation_status='needs_review';e.unresolved_reasons.append('Independent review flags unsupported claim or bad reference')
            row.update(e.to_dict())
        unresolved=[dict(evidence_id=r['evidence_id'],reasons=r['unresolved_reasons']) for r in rows if r['validation_status']!='accepted']
        save(dest/'evidence.json',rows);save(dest/'unresolved.json',unresolved);render(company,rows,unresolved,dest/'evidence_report.html')
        for doc in company['documents']:
            ds=[r for r in rows if r['document_id']==doc['document_id']]
            assessed=[r for r in ds if reviewed(reviews.get(r['evidence_id'],{}),doc['document_type'])]
            admitted=[r for r in ds if r['validation_status']=='accepted']
            correct=[r for r in assessed if all(reviews[r['evidence_id']][k] in ('correct','not_applicable') for k in review_checks(doc['document_type']))
                and not reviews[r['evidence_id']]['critical_unsupported'] and reviews[r['evidence_id']]['correct_source_reference']]
            ref=references.get(doc['document_id'],{});labels=ref.get('material_disclosures',[])
            ref_ready=bool(ref.get('reviewer') and ref.get('reviewed_at') and ref.get('independent') is True and ref.get('all_pages_reviewed') is True and labels)
            if ref_ready:
                ids=[label['reference_id'] for label in labels]
                if len(ids)!=len(set(ids)):raise ValueError('Duplicate reference IDs')
                source_pages={p['page_or_slide_number']:p['selected_text'] for p in canonical.get(doc['document_id'],{}).get('pages',[])}
                for label in labels:
                    if not label.get('quote') or not label.get('match_adjudicator'):raise ValueError('Incomplete independent reference')
                    if label['quote'] not in source_pages.get(label['physical_page'],''):raise ValueError('Reference quote not anchored in original')
                    if set(label['matched_evidence_ids'])-set(r['evidence_id'] for r in ds):raise ValueError('Unknown reference match')
            correct_ids={r['evidence_id'] for r in correct}
            matched=sum(bool(set(l['matched_evidence_ids']) & correct_ids) for l in labels) if ref_ready else None
            result=dict(company_id=company['company_id'],document_id=doc['document_id'],document_type=doc['document_type'],
                observations_reviewed=len(assessed),accepted_observations=len(admitted),fully_correct_observations=len(correct) if assessed else None,
                fully_correct_financial_observations=sum(r['reported_value'] is not None for r in correct) if assessed else None,
                incorrect_values=sum(reviews[r['evidence_id']]['value']=='incorrect' for r in assessed) if assessed else None,
                incorrect_period_unit_scope=sum(any(reviews[r['evidence_id']][k]=='incorrect' for k in ('period','unit','scope')) for r in assessed) if assessed else None,
                incorrect_source_attribution=sum(reviews[r['evidence_id']]['attribution']=='incorrect' for r in assessed) if assessed else None,
                unsupported_accepted_claims=sum(reviews[r['evidence_id']]['critical_unsupported'] for r in admitted),
                correct_source_references=sum(reviews[r['evidence_id']]['correct_source_reference'] for r in assessed) if assessed else None,
                unresolved_disclosures=len(ds)-len(admitted),material_reference_count=len(labels) if ref_ready else None,
                material_omissions=len(labels)-matched if ref_ready else None,
                material_recall=matched/len(labels) if ref_ready else None,
                reviewed_factual_correctness=len(correct)/len(assessed) if assessed else None)
            results.append(result)
    if set(reviews)-known_ids:raise ValueError('Review refers to unknown evidence')
    summary=dict(status='INDEPENDENT_REVIEW_PARTIAL',per_document=results,acceptance_achieved=None,
        note='No aggregate acceptance until every document has independent material references and reviewed observations')
    if all(r['observations_reviewed'] and r['material_recall'] is not None for r in results):
        summary['status']='INDEPENDENT_REVIEW_COMPLETE'
        summary['acceptance_achieved']=all(r['reviewed_factual_correctness']>=.95 and r['material_recall']>=.90 and
            r['unsupported_accepted_claims']==0 and r['correct_source_references']==r['observations_reviewed'] for r in results)
    save(output/'evaluation_summary.json',summary)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for arg in ('manifest','run','reviews','references','output'):p.add_argument('--'+arg,required=True)
    a=p.parse_args();print(evaluate(a.manifest,a.run,a.reviews,a.references,a.output)['status'])
