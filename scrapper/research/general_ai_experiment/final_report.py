"""Read-only analysis of preserved trials. No network, inference or label edits."""
from collections import defaultdict
from decimal import Decimal
import csv
import json
from pathlib import Path

from .runner import OUT, ROOT, dump, load, now, sha
from .protocol import MODELS, LOCAL, JEV, FIELDS
from .holdout_score import run as holdout_score, overlap, value_present


def repeats():
    groups = defaultdict(list)
    for path in sorted((OUT / 'runs').glob('repeat*-pages-*/*.json')):
        r = load(path)
        if not r.get('document_id') or not r.get('response'):
            continue
        raw = r['response']['choices'][0]['message']['content']
        events = json.loads(raw)['events'] if r.get('schema_valid') else []
        groups[(r['requested_model'], r['document_id'], r['page'])].append({
            'file': str(path.relative_to(OUT)), 'input_sha256': r['input_sha256'],
            'schema_valid': r.get('schema_valid'), 'events': events})
    output = []
    for (model, doc, page), rows in groups.items():
        comparisons = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                fields = [{json.dumps(e, sort_keys=True) for e in r['events']} for r in (a, b)]
                evidence = [{(e['event_type'], e['source_page'], e['exact_source_quote']) for e in r['events']} for r in (a, b)]
                def jaccard(pair):
                    return len(pair[0] & pair[1]) / len(pair[0] | pair[1]) if pair[0] | pair[1] else 1
                comparisons.append({'files': [a['file'], b['file']],
                    'identical_input': a['input_sha256'] == b['input_sha256'],
                    'strict_field_set_jaccard': jaccard(fields),
                    'category_page_quote_set_jaccard': jaccard(evidence)})
        output.append({'model': model, 'document_id': doc, 'page': page,
            'completed_replicates': len(rows), 'event_counts': [len(r['events']) for r in rows],
            'unique_input_hashes': sorted({r['input_sha256'] for r in rows}),
            'comparisons': comparisons})
    dump(OUT / 'repeatability-results.json', {'at': now(), 'results': output,
        'interpretation': 'Exact-set variation is not semantic error. Compare only pairs with identical input hashes. Temperature is omitted where unsupported; no determinism assumption.'})
    return output


def classification():
    models = defaultdict(list)
    passages = defaultdict(dict)
    for path in sorted((OUT / 'classification-results').glob('*.json')):
        r = load(path)
        models[r['requested_model']].append(r)
        passages[r['passage_id']][r['requested_model']] = r.get('classification')
    output = []
    for model, rows in models.items():
        output.append({'model': model, 'attempts': len(rows),
            'valid_classifications': sum(r.get('classification_schema_valid') is True for r in rows),
            'seconds': sum(r.get('seconds', 0) for r in rows),
            'input_tokens': sum(r.get('response', {}).get('usage', {}).get('input_tokens', r.get('response', {}).get('usage', {}).get('prompt_tokens', 0)) for r in rows),
            'output_tokens': sum(r.get('response', {}).get('usage', {}).get('output_tokens', r.get('response', {}).get('usage', {}).get('completion_tokens', 0)) for r in rows),
            'actual_usd': str(sum((Decimal(str(r.get('response', {}).get('usage', {}).get('cost', 0))) for r in rows), Decimal(0)))})
    detailed = []
    for passage, predictions in passages.items():
        corresponding = next(r for rows in models.values() for r in rows if r['passage_id'] == passage)
        detailed.append({'passage_id': passage, 'predictions': predictions,
            'all_category_agree': len({r['event_category'] for r in predictions.values() if r}) == 1,
            'all_direction_agree': len({r['event_direction'] for r in predictions.values() if r}) == 1,
            'deterministic': corresponding['deterministic']})
    result = {'at': now(), 'results': output, 'passages': detailed,
        'label_status': 'No independently adjudicated gold labels; agreement is not accuracy. Jev probabilities are not calibrated by six examples.',
        'excluded_from_document_reading_rank': True}
    dump(OUT / 'classification-summary.json', result)
    return result


def native_reference_coverage():
    """Same limited source/figure discovery measure, not native event accuracy."""
    refs = load(OUT / 'holdout-references.json')['references']
    manifest = {r['document_id']: r for r in load(OUT / 'holdout-final/manifest.json')['documents']}
    details = []
    for ref in refs:
        baseline = load(OUT / 'holdout-final' / manifest[ref['document_id']]['content_sha256'] / 'baseline.json')
        profiles = {}
        for profile in ('records', 'focused_records'):
            matches = []
            for record in baseline[profile]:
                evidence = [e for e in record.get('evidence', []) if e.get('page') == ref['source_page']]
                if any(overlap(e.get('quote', ''), ref['source_quote']) or value_present(ref['reported_value'], e.get('quote', '')) for e in evidence):
                    matches.append({'record_id': record['record_id'], 'claim_type': record.get('claim_type'),
                        'metric': record.get('metric'), 'original_value': record.get('original_value'),
                        'claim_text': record.get('claim_text')})
            profiles[profile] = matches
        details.append({'reference_id': ref['reference_id'], 'profiles': profiles})
    result = {'at': now(), 'scope': 'Same seven provisional source facts. Evidence coverage alone does not establish event extraction, attribution or interpretation accuracy.',
        'reference_denominator': len(refs), 'details': details,
        'complete_supported_event_precision': None, 'complete_supported_event_recall': None}
    dump(OUT / 'native-holdout-reference-coverage.json', result)
    return result


def availability():
    catalog = {r['requested_id']: r for r in load(OUT / 'availability.json')}
    policies = load(OUT / 'capability-review.json')
    output = []
    for model in MODELS:
        c = catalog.get(model, {})
        trials = [load(p) for p in (OUT / 'runs').glob('*/*.json')]
        trials = [r for r in trials if r.get('requested_model') == model and r.get('document_id')]
        endpoints = c.get('endpoints', {}).get('data', {}).get('endpoints', [])
        output.append({'model': model, 'catalog_available': c.get('catalog_available'),
            'canonical_slug': c.get('canonical_slug'), 'advertised_input_modalities': c.get('input_modalities'),
            'selected_provider': policies.get(model),
            'live_endpoint_prices': [{k: e.get(k) for k in ('tag', 'pricing', 'context_length', 'supported_parameters')} for e in endpoints],
            'completed_document_responses': sum(bool(r.get('response')) for r in trials),
            'valid_document_responses': sum(r.get('schema_valid') is True for r in trials),
            'http_failure_codes': sorted({r['http_status'] for r in trials if r.get('http_status')}),
            'other_failure_types': sorted({r['failure_type'] for r in trials if r.get('failure_type') and not r.get('http_status')}),
            'native_file_advertised': c.get('native_file_advertised'),
            'native_engine_observability': 'Requested native; provider internal preprocessing/version not independently observable.'})
    output.append({'model': JEV, 'chat_catalog_available': False, 'exact_id_endpoint_resolves': True,
        'actual_classification_execution_succeeded': True, 'api': '/api/v1/systemone',
        'provider': 'TypeSafe', 'returned_model': 'typesafe/jev-1.13-20260917',
        'price_input_usd_per_million': '0.042', 'price_output_usd_per_million': '0',
        'purpose': 'Secondary typed passage classification only; excluded from direct document ranking.'})
    local = OUT / 'runs/local-smoke/status.json'
    failure = OUT / 'runs/local-smoke/failure.json'
    output.append({'model': LOCAL, 'local': True, 'status': load(local) if local.exists() else None,
        'failure': load(failure) if failure.exists() else None, 'no_substitution': True})
    dump(OUT / 'availability-final.json', output)
    return output


def examples():
    cases = [
        ('solar_layout_false_negative', 'anthropic/claude-haiku-5.5', '00032a', 17, 'Commissioned', None,
         'Original page visibly supports commissioning 2.6 MW. Flat native reading order interleaves a side card; exact-span verifier rejects a correct visual quotation. Verifier limitation, not established fabrication.'),
        ('issuer_rating_layout_false_negative', 'xiaomi/mimo-v2.6-flash', '00162b', 1, 'AAA', 'rating',
         'Original table shows issuer AAA Stable reaffirmed, distinct from AA+ Stable assigned subdebt. Reconstructed table row may fail flat native-span matching. Requires source-region review.'),
        ('market_share_is_not_orders', 'xiaomi/mimo-v2.6-flash', '00162b', 1, 'share', 'orders',
         'Provisional category error: market share is not an order book, inflow or execution event. Quotation support alone does not establish category correctness.'),
        ('agency_expectation_is_not_management_guidance', 'xiaomi/mimo-v2.6-flash', '00162b', 1, '', 'guidance',
         'Provisional category/attribution error: ICRA expectation is not management guidance under the common task definition.'),
        ('carbon_statement_not_liquidity', 'anthropic/claude-haiku-5.5', '00032a', 17, 'footprint', 'liquidity_leverage_working_capital',
         'Provisional category error: lower carbon footprint/cost efficiency does not by itself establish liquidity, leverage or working-capital development.'),
        ('mexico_award_source_example', 'anthropic/claude-haiku-5.5', '000122', 5, '6.5', 'orders',
         'Source-supported award amount. Annual business value must stay distinct from total order book; supply timing is forward-looking. Full event still needs independent adjudication.'),
    ]
    sources = {r['document_id']: r for r in load(OUT / 'holdout-final/manifest.json')['documents']}
    output = []
    for identifier, model, prefix, page, phrase, category, note in cases:
        for path in sorted((OUT / 'runs/holdout-pages-structured-v2').glob('*.json')):
            r = load(path)
            if r.get('requested_model') != model or not r.get('document_id', '').removeprefix('sha256:').startswith(prefix) or r.get('page') != page:
                continue
            qualified = load(OUT / 'reverified' / path.parent.name / path.name)['proposals']
            for proposal in qualified:
                event = proposal['event']
                if (category is None or event['event_type'] == category) and phrase.lower() in event['exact_source_quote'].lower():
                    source = sources[r['document_id']]
                    output.append({'case_id': identifier, 'status': 'Codex provisional source review; not independent gold',
                        'finding': note, 'model': model, 'event': event,
                        'verification': proposal, 'raw_result': str(path.relative_to(OUT)),
                        'original_pdf': source['path'], 'physical_pdf_page': page,
                        'source_image': str((Path('artifacts/phase5/general-ai-experiment/holdout-final') / source['content_sha256'] / f'page-{page}.png'))})
                    break
    dump(OUT / 'source-linked-examples.json', output)
    return output


def per_document():
    groups = defaultdict(list)
    for p in sorted((OUT / 'runs').glob('*/*.json')):
        r = load(p)
        if r.get('document_id') and r.get('strategy'):
            groups[(r['requested_model'], p.parent.name, r['strategy'], r['document_id'])].append(r)
    output = []
    for (model, stage, strategy, document_id), rows in groups.items():
        usage = [(r.get('response') or {}).get('usage') or {} for r in rows]
        output.append({'model': model, 'stage': stage, 'strategy': strategy, 'document_id': document_id,
            'attempted_windows': len(rows), 'schema_valid_windows': sum(r.get('schema_valid') is True for r in rows),
            'seconds_including_failed_attempts': sum(r.get('seconds', 0) for r in rows),
            'input_tokens': sum(u.get('prompt_tokens', 0) for u in usage) + sum(r.get('input_tokens', 0) for r in rows),
            'output_tokens': sum(u.get('completion_tokens', 0) for u in usage) + sum(r.get('output_tokens', 0) for r in rows),
            'known_billed_usd': str(sum((Decimal(str(u['cost'])) for u in usage if u.get('cost') is not None), Decimal(0))),
            'cost_status': 'local_no_api_charge' if stage.startswith('local') else 'failed_attempt_billing_may_be_unresolved' if any(r.get('status') == 'failed' for r in rows) else 'response_usage',
            'local_peak_mlx_bytes': max((r.get('mlx_peak_memory_bytes', 0) for r in rows), default=0) or None,
            'local_rss_highwater_bytes': max((r.get('rss_highwater_bytes', 0) for r in rows), default=0) or None,
            'scope': 'Selected windows are not whole-document visual coverage. Counts are not correctness.'})
    dump(OUT / 'per-document-results.json', output)
    return output


def review_queue():
    fields = ['run', 'result_file', 'model', *FIELDS, 'source_verification',
        'semantic_review', 'historical_ml_eligible', 'verification_details']
    with (OUT / 'proposal-review-queue.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for p in sorted((OUT / 'reverified').glob('*/*.json')):
            original = OUT / 'runs' / p.parent.name / p.name
            r = load(original)
            for proposal in load(p)['proposals']:
                writer.writerow({'run': p.parent.name, 'result_file': str(original.relative_to(OUT)),
                    'model': r['requested_model'], **proposal['event'],
                    'source_verification': proposal['source_verification'],
                    'semantic_review': 'pending', 'historical_ml_eligible': False,
                    'verification_details': json.dumps({k: v for k, v in proposal.items() if k != 'event'}, ensure_ascii=False)})


def write_results(report):
    artifact = '../../../artifacts/phase5/general-ai-experiment/'
    lines = ['# Executed Phase 5 architecture experiment', '',
        'Recommendation: **retain Option A operationally**. This bounded run does not establish a replacement winner. Existing Phase 5 code remains unchanged; no modules were deleted and no model events were admitted to research features or historical ML.', '',
        '## 1. Model/provider availability', '',
        'All nine exact cloud IDs were present in the live catalog. Availability is distinct from successful inference. Prices, canonical model slugs, provider tags, supported parameters and observed failures are in [availability-final.json]('+artifact+'availability-final.json).', '']
    for r in load(OUT / 'availability-final.json'):
        if r.get('local'):
            lines.append('- `'+r['model']+'`: verified exact checkpoint loaded; first source-packet inference timed out with no completed output. See local measurements below; no substitute.')
        elif r['model'] == JEV:
            lines.append('- `'+JEV+'`: exact Decisions endpoint resolves and all six classifications completed; response identifies `typesafe/jev-1.13-20260917`. Absent from chat catalog; no silent substitution.')
        else:
            provider = (r.get('selected_provider') or {}).get('endpoint_tag')
            lines.append(f"- `{r['model']}` via `{provider}`: {r['valid_document_responses']} valid / {r['completed_document_responses']} returned document responses across all preserved stages; HTTP failures {r['http_failure_codes']}; other failures {r['other_failure_types']}.")
    lines += ['', 'Free Gemma requests reached upstream rate limits (429). Earlier synthetic ZDR-only free probes returned 404; these are distinct routing observations. Mistral and MiMo Pro had partial smoke success plus 429s, so did not pass the three-family main-stage gate. The reviewed Gemini Vertex Flex routes returned invalid-argument failures even after the one recorded transport correction. These observations do not prove global model incompatibility across every possible provider.', '',
        '## 2. Actual prompt and inputs', '',
        '[Common task]('+artifact+'development/prompt.txt) and [protocol/schema](../../../scrapper/research/general_ai_experiment/protocol.py) are preserved. Native input uses OpenRouter chat content `file.file_data` with original PDF base64 and `file-parser.pdf.engine=native`. Page input uses full original native text and one selected original full-frame PNG, with page IDs, table/structural information and image omissions. No generated summaries, search or tool calls are supplied. Exact input hashes, requested schema/reasoning settings, provider and returned version/usage are recorded per response. Native engine actually used remains unestablished because the response does not expose sufficient evidence; native requests are labelled accordingly.', '',
        'Selected page rendering is 144 DPI, maximum 1280 pixels per side, aspect ratio preserved, no crop. Main scope is 17 selected windows on five originals, not all page images. Cloud output cap is 8192 tokens. The local runner used 4096 tokens and an intended 120-second signal ceiling that overran; actual runtime and the subsequent watchdog correction are disclosed below. Structured transport corrections are common, with only necessary endpoint-format changes; original failed smoke attempts remain separate.', '',
        'See [README](README.md) for reuse interfaces, sharing review, budget controls and reproducible commands. The [PDF documentation](https://openrouter.ai/docs/guides/overview/multimodal/pdfs) documents parser selection and the default OCR eight-image forwarding limitation. That default OCR route was not used.', '',
        '## 3. Per-model document results', '',
        '[Measured stage results]('+artifact+'measured-results.json) and [per-document tokens, runtime and billed cost]('+artifact+'per-document-results.json) retain successful and failed requests. Counts below are transport/source-verification measurements, **not financial-event accuracy**. Source rejection can reflect extraction layout, a compound numeric format, or unsupported semantics; it is not automatically hallucination.', '']
    for r in report['metrics']:
        if r['stage'].startswith(('main', 'holdout', 'local')):
            lines.append(f"- `{r['model']}` / {r['stage']}: {r['schema_valid_requests']}/{r['request_count']} schema-valid attempts; {r['proposed_events']} proposed, {r['source_matched_proposals']} source-matched, {r['mechanically_rejected_proposals']} mechanically rejected; {r['seconds']:.2f} seconds including failures; known billed ${r['actual_reported_cost_usd']}.")
    lines += ['', 'The incumbent standard and focused extractors ran on the same frozen originals. Development counts are 1122 standard and 2344 focused records across 145 pages; output volume is not accuracy. Native/reference coverage is in [native-holdout-reference-coverage.json]('+artifact+'native-holdout-reference-coverage.json). Both native profiles have some evidence on four of seven reference windows/facts, but this broad evidence-coverage check does not establish the extracted event, metric or relationship is correct and is not a precision score.', '',
        '## 4. Financial accuracy and hallucinations', '',
        '**Complete supported-event precision/recall, event-category precision/recall, numeric accuracy, unit correctness, company/segment/facility correctness, period/actual-versus-forecast correctness, chart relationship accuracy and hallucination rates are not established.** There is no exhaustive independently adjudicated gold set. These metrics are null in the machine-readable results rather than invented from quotation matches. Schema rates, abstentions, latency, tokens, usage costs and mechanical rejection reasons are measured separately. No composite score or direct-document accuracy ranking is claimed.', '',
        'Source evidence, semantic status and historical eligibility remain separate. All proposed events require semantic review and have historical ML eligibility false. Matching a quote does not prove a facility relationship, rating action, metric association or forecast interpretation. Original inconsistent statements are preserved; no model repairs source values.', '',
        '## 5. Jev secondary classification benchmark', '',
        '[Classification results]('+artifact+'classification-summary.json) compare six identical original source-verified passages. Jev uses proper `state` and typed `choice` questions at `/api/v1/systemone`, as documented in [the Decisions reference](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request). General models receive the same passages and classification definitions. No PDF is sent to Jev. Existing native categories are cross-walked only from matching primary evidence; its direction field is unsupported, not invented.', '',
        'All four classifiers agreed on six of six categories. Directions agreed on five of six: static “adequate liquidity” was classified not stated by Jev, neutral by MiMo, and positive by Haiku/DeepSeek. This is agreement, not accuracy or calibrated confidence. There is no demonstrated incremental accuracy benefit justifying a Jev dependency.', '']
    for r in load(OUT / 'classification-summary.json')['results']:
        lines.append(f"- `{r['model']}`: {r['valid_classifications']}/{r['attempts']} valid; {r['seconds']:.2f} seconds total; ${r['actual_usd']} total.")
    lines += ['', '## 6. Source-linked correct and incorrect examples', '',
        '[Open source-review examples]('+artifact+'source-linked-examples.html) or inspect [the full qualified events]('+artifact+'source-linked-examples.json). Findings are Codex provisional judgments, not independent human ground truth.', '',
        '- Correct visual quotation rejected: Amines & Plasticizers physical PDF page 17 says a 2.6 MW captive solar plant was commissioned. Flat native extraction interleaves a side card within the sentence, causing exact-span failure. Do not count this as fabrication.',
        '- Correct table relationship requiring region review: Niva Bupa page 1 distinguishes issuer AAA Stable reaffirmed from 500-crore subdebt AA+ Stable assigned. A reconstructed row can fail flat text matching.',
        '- Provisional wrong event category: MiMo labelled a market-share disclosure as orders.',
        '- Provisional attribution/category error: MiMo labelled an ICRA profitability expectation as management guidance.',
        '- Provisional wrong event category: Haiku labelled a lower-carbon/cost-efficiency statement as liquidity/leverage/working capital.',
        '- Useful source discovery: Uniparts annual Mexico business award of $6.5 million, with future supply timing. Annual award value must remain distinct from total order book.', '',
        '## 7. Actual cost and ceiling', '',
        f"Known response-reported OpenRouter inference cost: **${report['known_actual_cost_usd']}**. Actual billing is complete: **{report['actual_cost_complete']}**. Actual plus all unresolved maximum reservations: **${report['actual_plus_unresolved_max_reservations_usd']}**, against the $10 ceiling. Failure/timeout charges are not silently assumed zero. [Ledger]({artifact}spend-ledger.json) is authoritative for conservative request admission.", '',
        'No paid OCR parsing was enabled; local preprocessing has no OpenRouter fee. Electricity, internet and hardware depreciation are unmetered, so total economic preprocessing/local cost is unknown. No key/account limit was altered.', '',
        '## 8. MacBook runtime and memory', '',
        'Host is Apple Silicon arm64 macOS with 24 GB unified memory. Development canonical preparation/rendering took 13.209 seconds and 556,646,400 bytes process peak RSS. Isolated runtime pins are in [requirements.runtime.txt](requirements.runtime.txt). See local status/results in [availability-final.json]('+artifact+'availability-final.json) and [per-document measurements]('+artifact+'per-document-results.json). Failed download attempts are retained. Only the exact small requested checkpoint is eligible; no massive substitute was downloaded.', '',
        'The exact Qwen checkpoint completed download and matched its published weight SHA-256. It loaded but produced no completed output on the first full rating packet. MLX peak allocation was 4,331,380,386 bytes (4.03 GiB); process peak RSS was 1,722,417,152 bytes (1.60 GiB). Observed request elapsed time was approximately 495.92 seconds: the initial 120-second Python alarm was delayed inside native MLX work. This was a timeout-control failure, not a successful bounded 120-second trial. A separate 125-second whole-worker subprocess watchdog was added afterward; it has not been validated on another model inference run. No reduced-context rerun is pooled into equal-input results. The other local smoke families and local-only originals have no completed local results.', '',
        '## 9. Fresh holdout and repeats', '',
        'Three original local-archive PDFs were frozen before output inspection: Uniparts Q2 FY25 transcript, Amines & Plasticizers Q1 FY26 presentation, ICRA Niva Bupa. They contain 67 pages. References cover seven provisional facts in six selected windows. Hash exclusion and content-overlap audits exclude prior diagnostics; this is operationally fresh, not guaranteed absent from model training. A restricted candidate presentation was excluded before transmission. No rating-agency files were retrieved again.', '',
        'The source/figure discovery diagnostic is deliberately limited. Haiku and MiMo page inputs each mechanically recover five of seven frozen facts; two are layout-related false negatives described above. Haiku original-PDF input mechanically recovers one of seven under this exact-span method. DeepSeek returned no valid holdout document response before timeout, so holdout accuracy is unmeasurable. These are not 71%, 14% or 0% financial-accuracy scores. See [holdout-provisional-agreement.json]('+artifact+'holdout-provisional-agreement.json). The verifier and references were not tuned after holdout outputs.', '',
        'Three repeat rounds are preserved in [repeatability-results.json]('+artifact+'repeatability-results.json). On the same transcript-page input, Haiku emitted 5/5/7 events and MiMo 8/10/10. On the rating page, Haiku emitted 6/5/7 and MiMo 7/7/7; equal counts do not imply equal fields. DeepSeek emitted 6/6/6, but had two distinct payload hashes because live price filters changed; only matching-hash pairs qualify as identical-input repeats. Exact field-set and category/page/quote-set agreement are reported without treating variation as error.', '',
        '## 10. Architecture decision', '',
        'Option A stays operational because independently checked superiority is unproven. This does not establish the incumbent as an accuracy winner. Option B remains a useful research candidate: source-grounded general proposals found complex disclosures cheaply, but semantic category errors and verification gaps remain. Option C has no demonstrated incremental benefit sufficient to justify additional classification/routing complexity. Do not assume hybrid wins.', '',
        'The next deciding experiment needs exhaustive independently reviewed event labels and a new frozen holdout after any verifier/prompt changes. These holdout documents are now diagnostics and cannot become unseen again. Region-aware evidence verification is a research need, not permission to weaken source checks.', '',
        '## 11. Retirement analysis only', '',
        '[Module-level inventory](module-inventory.json) records retention/narrower reuse/test tooling and static direct importers. **No module is currently eligible for retirement.** Retain acquisition, immutable archive, source evidence, provenance, verifier, temporal integrity and historical ML restrictions. Dynamic/external dependency absence has not been established.', '',
        'Conditional future plan: after demonstrated independently checked unseen superiority, keep the incumbent selectable, shadow-replay frozen fixtures, test downstream consumers and historical admission, then remove only demonstrably redundant interpretation responsibilities in small reversible changes with rollback. No deletion or production migration was performed.', '',
        'Validation: 83 targeted tests passed. Frozen development/holdout inputs, holdout references/configuration and pre-holdout verifier SHA were rechecked unchanged. Existing `scrapper/research/document_intelligence/` has no diff from this experiment. API keys, runtime and checkpoint weights are not committed.', '']
    reconciliation = OUT / 'cost-reconciliation.json'
    if reconciliation.exists():
        r = load(reconciliation)
        lines += [f"Final key-level usage increase: **${r['key_usage_increase_usd']}**, versus **${r['known_response_cost_usd']}** in completed response records; unallocated difference **${r['difference_from_known_responses_usd']}**. Key usage can include concurrent activity, so it is not a per-failure allocation. No account/key limit was changed. See [cost-reconciliation.json]({artifact}cost-reconciliation.json).", '']
    (ROOT / 'docs/phase5/general-ai-experiment/RESULTS.md').write_text('\n'.join(lines))


def main():
    holdout = holdout_score()
    repeats()
    classification()
    native_reference_coverage()
    availability()
    examples()
    per_document()
    review_queue()
    measured = load(OUT / 'measured-results.json')
    report = {'at': now(), 'status': 'bounded_research_executed_no_replacement_proven',
        'recommendation': 'Option A remains operational. Neither general-model-first nor hybrid has demonstrated superior independently adjudicated unseen accuracy.',
        'development_originals': 11, 'development_pages': 145,
        'cloud_development_originals': 5, 'local_only_development_originals': 6,
        'holdout_originals': 3, 'holdout_pages': 67, 'holdout_reference_facts': 7,
        'holdout_label_status': holdout['label_status'],
        'known_actual_cost_usd': measured['actual_reported_inference_cost_usd'],
        'actual_cost_complete': measured['actual_cost_complete'],
        'actual_plus_unresolved_max_reservations_usd': measured['actual_plus_unresolved_max_reservations_usd'],
        'ceiling_usd': '10', 'metrics': measured['model_results'],
        'local_status': load(OUT / 'availability-final.json')[-1],
        'retired_modules': [], 'existing_phase5_modified': False,
        'limitations': ['No independent human adjudication or exhaustive semantic gold labels.',
            'Six restricted rating originals not transmitted externally.',
            'DeepSeek main and holdout coverage incomplete due provider timeouts.',
            'Native engine requested explicitly; internal provider processing not independently observable.',
            'Canonical flat-text layout causes genuine source-verification false negatives.',
            'Raw malformed outputs, uncertain billing and failures retained; no automatic acceptance into ML.']}
    dump(OUT / 'report.json', report)
    write_results(report)
    print(json.dumps({'status': report['status'], 'known_actual_cost_usd': report['known_actual_cost_usd']}))


if __name__ == '__main__':
    main()
