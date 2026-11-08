"""Limited agreement with pre-frozen provisional references, never ground truth."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import re
from .runner import OUT, load, dump, now
from .results import summarize
from scrapper.research.document_intelligence.evaluation import compact
from scrapper.research.document_intelligence.numbers import NUMBER, normalize_value


def value_present(value, text):
    if value is None:
        return None
    try:
        target = Decimal(value.replace(',', ''))
        return any(Decimal(m.group().replace(',', '')) == target for m in re.finditer(NUMBER, text or ''))
    except InvalidOperation:
        return value in (text or '')


def overlap(left, right):
    a, b = compact(left), compact(right)
    return min(len(a), len(b)) >= 12 and (a in b or b in a)


def run():
    summarize()  # One current deterministic verifier for every preserved response.
    refs = load(OUT / 'holdout-references.json')['references']
    groups = defaultdict(list)
    for path in (OUT / 'runs').glob('holdout-*/*.json'):
        row = load(path)
        if not row.get('requested_model') or not row.get('document_id'):
            continue
        derived = OUT / 'reverified' / path.parent.name / path.name
        proposals = load(derived)['proposals'] if derived.exists() else []
        groups[(row['requested_model'], row['strategy'])].append((row, proposals))
    results = []
    for (model, strategy), rows in sorted(groups.items()):
        records = [p for _, ps in rows for p in ps]
        coverage = {r['document_id']: set() for r, _ in rows}
        for row, _ in rows:
            if row.get('schema_valid'):
                coverage[row['document_id']].update([r['source_page'] for r in refs if r['document_id'] == row['document_id']] if row['page'] == 'full' else [row['page']])
        details = []
        for ref in refs:
            scoped = [p for p in records if p['event']['document_id'] == ref['document_id'] and p['event']['source_page'] == ref['source_page']]
            discovered = [p for p in scoped if p['checks'].get('exact_native_quote') and
                          (overlap(p['canonical_evidence'][0]['quote'], ref['source_quote']) or value_present(ref['reported_value'], p['canonical_evidence'][0]['quote']))]
            categorized = [p for p in discovered if p['event']['event_type'] == ref['event_type']]
            numeric = [p for p in categorized if value_present(ref['reported_value'], p['event']['reported_value'])]
            unit_results = []
            for p in numeric:
                target = normalize_value(ref['reported_value'], unit_hint=(ref['unit'] or '').replace(' annually', '')) if ref['unit'] else None
                actual = normalize_value(p['event']['reported_value'] or '', unit_hint=p['event']['unit'])
                if target and actual:
                    unit_results.append(all(target[k] == actual[k] for k in ('currency', 'unit', 'scale')))
            details.append({'reference_id': ref['reference_id'], 'page_input_covered': ref['source_page'] in coverage.get(ref['document_id'], set()),
                'source_fact_discovered': bool(discovered), 'category_and_source_matched': bool(categorized),
                'reported_figure_matched': bool(numeric), 'same_currency_unit_scale_parsed': any(unit_results) if unit_results else None,
                'semantic_correctness_established': False, 'speaker_scope_time_action_review': 'pending',
                'model_events_for_review': [p['event'] for p in scoped]})
        n = len(refs)
        results.append({'model': model, 'strategy': strategy, 'reference_denominator': n,
            'covered_reference_cases': sum(r['page_input_covered'] for r in details),
            'source_fact_reference_recall': sum(r['source_fact_discovered'] for r in details) / n,
            'category_and_source_reference_recall': sum(r['category_and_source_matched'] for r in details) / n,
            'reported_figure_reference_recall': sum(r['reported_figure_matched'] for r in details) / n,
            'complete_supported_event_precision': None, 'complete_supported_event_recall': None,
            'details': details})
    report = {'at': now(), 'label_status': 'provisional_source_reviewed_references_no_independent_human_adjudication',
        'scope': 'Seven pre-frozen source facts in six windows; not exhaustive document event labels.',
        'interpretation': 'Discovery and figure agreement use canonical-source matching and the frozen reference quantities. They do not establish entity/facility, metric relationship, rating action, period, speaker or business-direction correctness. Missing responses count as missing coverage; no single composite score.',
        'results': results}
    dump(OUT / 'holdout-provisional-agreement.json', report)
    return report


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
