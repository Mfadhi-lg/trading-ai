"""Prepare, generate once (explicitly authorized), admit/export and replay credit PDFs."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import time
from pathlib import Path

from . import credit_v1 as v
from .credit_candidate_provider import (VERSION, STRUCTURED_VERSION, FULL_VERSION, response_schema, build_region_catalog, build_request,
    canonical_hash, generate_candidates, identity, load_candidate_bundle, sha_bytes, wire_bytes)
from .schema import DocumentContext

ROOT = Path(__file__).resolve().parents[3]
MODEL = 'google/gemini-3.8-flash'
PROVIDER = 'google-ai-studio/flex'
MAX_OUTPUT = 8192
CONTEXT_LIMIT = 1048576
INPUT_RATE = Decimal('0.375')  # USD per million; max_price pins these upper prices
OUTPUT_RATE = Decimal('1.875')
CEILING = Decimal('0.05')


def dump(path, value):
    with Path(path).open('x') as f:
        f.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def descriptor(path):
    return {'path': str(Path(path).resolve()), 'sha256': v.digest(path)}


def new_directory(output):
    output = Path(output).resolve()
    if not any(output.is_relative_to(p) for p in (ROOT / 'artifacts/credit-rating-intelligence-v1', Path('/tmp').resolve())):
        raise ValueError('Use a development artifact directory or /tmp')
    output.mkdir(parents=True, exist_ok=False)
    return output


def payload_for(request):
    response_format = {'type': 'json_object'}
    is_section = request.get('request_kind') == 'section_extraction'
    if is_section:
        from .credit_sections import section_response_schema
        schema = section_response_schema(request)
    else:
        schema = response_schema(request)
    if request.get('schema_version') in (STRUCTURED_VERSION, FULL_VERSION) or is_section:
        response_format = {'type': 'json_schema', 'json_schema': {
            'name': 'credit_disclosures', 'strict': True, 'schema': schema}}
    return {'model': MODEL, 'messages': [{'role': 'user', 'content': json.dumps(request, separators=(',', ':'), ensure_ascii=False)}],
            'max_tokens': 16384 if is_section else 65536 if request.get('schema_version') == FULL_VERSION else MAX_OUTPUT, 'reasoning': {'effort': 'low'}, 'stream': False,
            'response_format': response_format,
            'provider': {'only': [PROVIDER], 'allow_fallbacks': False, 'require_parameters': True,
                         'max_price': {'prompt': float(INPUT_RATE), 'completion': float(OUTPUT_RATE)}}}


def budget(payload):
    # Conservative admission allowance, not an exact tokenizer count.
    size = len(wire_bytes(payload))
    output_limit = payload.get('max_tokens', MAX_OUTPUT)
    ceiling = Decimal('0.35') if output_limit == 65536 else Decimal('0.05') if output_limit == 16384 else CEILING
    expected_output = 6000 if output_limit == 16384 else 20000 if output_limit == 65536 else 4096
    upper = size + 2048
    maximum = (Decimal(upper) * INPUT_RATE + Decimal(output_limit) * OUTPUT_RATE) / 1000000
    if upper + output_limit > CONTEXT_LIMIT or maximum > ceiling:
        raise ValueError('Request exceeds fixed context/spending reservation; do not truncate or retry')
    return {'wire_bytes': size, 'input_tokens_estimate': round(size / 3.5),
            'input_tokens_conservative_allowance': upper, 'context_limit': CONTEXT_LIMIT,
            'expected_completion_tokens_including_reasoning': expected_output,
            'max_completion_tokens_including_reasoning': output_limit,
            'estimated_cost_usd': float((Decimal(size) / Decimal('3.5') * INPUT_RATE + expected_output * OUTPUT_RATE) / 1000000),
            'conservative_max_cost_usd': float(maximum), 'reservation_usd': str(ceiling),
            'cost_scope': 'Provider inference USD; taxes/credit purchase fees excluded. No tools/search. No cache discount assumed.'}


def prepare_sections(prepared, output):
    """Freeze four compact requests from an existing untouched V1 preparation."""
    prepared = Path(prepared).resolve()
    with v.offline_guard():
        original_freeze, _request, _payload = verify_prepared(prepared)
        manifest = v.read_json(prepared/'manifest.json')
        ctx = DocumentContext(**manifest['context'])
        extraction = v.read_json(prepared/'canonical.json')
        catalog = v.read_json(prepared/'catalog.json')
        from .credit_sections import ASSIGNMENTS, build_section_material, build_section_request
        sidecar, section_manifest = build_section_material(ctx, extraction, catalog)
        output = new_directory(output)
        dump(output/'evidence-sidecar.json', sidecar)
        dump(output/'section-manifest.json', section_manifest)
        requests, payloads, budgets = {}, {}, {}
        for assignment in sorted(ASSIGNMENTS):
            request = build_section_request(ctx, assignment, sidecar)
            payload = payload_for(request)
            requests[assignment] = request
            payloads[assignment] = payload
            budgets[assignment] = budget(payload)
            dump(output/f'request-{assignment}.json', request)
            (output/f'payload-{assignment}.json').write_bytes(wire_bytes(payload))
        total_wire = sum(x['wire_bytes'] for x in budgets.values())
        total_input = sum(x['input_tokens_estimate'] for x in budgets.values())
        total_expected_cost = sum(Decimal(str(x['estimated_cost_usd'])) for x in budgets.values())
        total_max_cost = sum(Decimal(str(x['conservative_max_cost_usd'])) for x in budgets.values())
        # Compare the compact workflow with this document's own frozen full
        # request.  Earlier versions used Aether/Phase-5 constants here, which
        # made a new-company preparation carry experiment-specific metadata.
        baseline_tokens = original_freeze['budget']['input_tokens_estimate']
        baseline_wire = original_freeze['budget']['wire_bytes']
        freeze = {'schema_version': sidecar['schema_version'], 'source_preparation': descriptor(prepared/'freeze.json'),
            'source_sha256': ctx.content_sha256, 'canonical_sha256': canonical_hash(extraction),
            'catalog_sha256': canonical_hash(catalog), 'section_manifest_sha256': canonical_hash(section_manifest),
            'sidecar_sha256': canonical_hash(sidecar), 'model': MODEL, 'provider': PROVIDER,
            'assignments': {key: {'request': descriptor(output/f'request-{key}.json'),
                                  'payload': descriptor(output/f'payload-{key}.json'),
                                  'payload_sha256': sha_bytes(wire_bytes(payloads[key])),
                                  'budget': budgets[key]} for key in sorted(requests)},
            'measurement': {**section_manifest['coverage'], 'full_request_source_regions': len(catalog['regions']),
                'full_request_input_tokens_estimate': baseline_tokens,
                'full_request_payload_wire_bytes': baseline_wire,
                'section_payload_wire_bytes_total': total_wire, 'section_input_tokens_estimate_total': total_input,
                'estimated_prompt_token_reduction_pct': round(100 * (baseline_tokens-total_input)/baseline_tokens, 3),
                'payload_wire_reduction_pct': round(100 * (baseline_wire-total_wire)/baseline_wire, 3)},
            'cost': {'estimated_total_usd': float(total_expected_cost),
                     'conservative_max_total_usd': float(total_max_cost),
                     'hard_reservation_total_usd': str(sum(
                         (Decimal(x['reservation_usd']) for x in budgets.values()), Decimal('0'))),
                     'cost_scope': 'This frozen four-request section run only; no prior experiment cost is included.'},
            'inference_calls': 0, 'authorization': 'NOT AUTHORIZED; stop before section generation'}
        dump(output/'freeze.json', freeze)
        from .credit_sections import section_coverage_ledger
        dump(output/'section-coverage-ledger.json', section_coverage_ledger(section_manifest, requests))
        return freeze


def verify_sections(directory):
    directory = Path(directory).resolve()
    freeze = v.read_json(directory/'freeze.json')
    with v.offline_guard():
        source_freeze = v.checked_file(ROOT, freeze['source_preparation'])
        prepared = source_freeze.parent
        verify_prepared(prepared)
        manifest = v.read_json(prepared/'manifest.json')
        ctx = DocumentContext(**manifest['context'])
        extraction = v.read_json(prepared/'canonical.json')
        catalog = v.read_json(prepared/'catalog.json')
        from .credit_sections import ASSIGNMENTS, build_section_material, build_section_request
        from .credit_sections import LEGACY_VERSION, VERSION as SECTION_VERSION
        section_version = freeze.get('schema_version', LEGACY_VERSION)
        if section_version not in {LEGACY_VERSION, SECTION_VERSION}:
            raise ValueError('Unknown frozen section version')
        sidecar, section_manifest = build_section_material(ctx, extraction, catalog, version=section_version)
        if sidecar != v.read_json(directory/'evidence-sidecar.json') or section_manifest != v.read_json(directory/'section-manifest.json'):
            raise ValueError('Frozen section discovery changed')
        if (freeze['source_sha256'] != ctx.content_sha256 or freeze['canonical_sha256'] != canonical_hash(extraction) or
                freeze['catalog_sha256'] != canonical_hash(catalog) or
                freeze['section_manifest_sha256'] != canonical_hash(section_manifest) or
                freeze['sidecar_sha256'] != canonical_hash(sidecar)):
            raise ValueError('Frozen section identity changed')
        requests = {}
        budgets = {}
        for key in sorted(ASSIGNMENTS):
            request = build_section_request(ctx, key, sidecar)
            payload = payload_for(request)
            entry = freeze['assignments'][key]
            v.checked_file(ROOT, entry['request']); v.checked_file(ROOT, entry['payload'])
            if (request != v.read_json(directory/f'request-{key}.json') or
                    wire_bytes(payload) != (directory/f'payload-{key}.json').read_bytes() or
                    entry['payload_sha256'] != sha_bytes(wire_bytes(payload)) or entry['budget'] != budget(payload)):
                raise ValueError('Frozen section request changed')
            requests[key] = request
            budgets[key] = entry['budget']
        if (freeze['measurement']['section_payload_wire_bytes_total'] != sum(x['wire_bytes'] for x in budgets.values()) or
                freeze['measurement']['section_input_tokens_estimate_total'] != sum(x['input_tokens_estimate'] for x in budgets.values()) or
                freeze['measurement']['source_text_coverage_pct'] != section_manifest['coverage']['source_text_coverage_pct'] or
                freeze['cost']['estimated_total_usd'] != float(sum(Decimal(str(x['estimated_cost_usd'])) for x in budgets.values())) or
                freeze['cost']['conservative_max_total_usd'] != float(sum(Decimal(str(x['conservative_max_cost_usd'])) for x in budgets.values())) or
                freeze['inference_calls'] != 0 or freeze['authorization'] != 'NOT AUTHORIZED; stop before section generation'):
            raise ValueError('Frozen aggregate measurement or authorization changed')
    return freeze, ctx, extraction, sidecar, requests


def generate_section(directory, assignment, authorized_hash, ceiling):
    """One-shot section send. Each of the four payloads needs exact authorization."""
    directory = Path(directory).resolve()
    freeze, _ctx, _ext, _sidecar, requests = verify_sections(directory)
    if assignment not in requests:
        raise ValueError('Unknown assignment')
    entry = freeze['assignments'][assignment]
    if authorized_hash != entry['payload_sha256'] or Decimal(ceiling) != Decimal(entry['budget']['reservation_usd']):
        raise ValueError('Explicit exact-payload authorization and fixed reservation required')
    response_path = directory/f'response-{assignment}.raw.json'
    bundle_path = directory/f'candidate-bundle-{assignment}.json'
    reservation_path = directory/f'generation-reservation-{assignment}.json'
    failure_path = directory/f'generation-failure-{assignment}.json'
    if any(p.exists() for p in (response_path, bundle_path, reservation_path, failure_path)):
        raise FileExistsError('Section generation already reserved; no overwrite or repeat send')
    from ..general_ai_experiment.runner import api_key, request as transport
    key = api_key()
    if not key:
        raise ValueError('OPENROUTER_API_KEY missing (environment or repository .env)')
    payload = json.loads((directory/f'payload-{assignment}.json').read_text())
    dump(reservation_path, {'assignment_id': assignment, 'payload_sha256': authorized_hash,
                            'reserved_usd': entry['budget']['reservation_usd'], 'attempts': 1})
    def provider(source_request):
        if source_request != requests[assignment]:
            raise ValueError('Section request changed before send')
        started = time.monotonic()
        raw = transport('/chat/completions', payload=payload, key=key, return_raw=True)
        response_path.write_bytes(raw)
        receipt = {'model': MODEL, 'provider': PROVIDER, 'assignment_id': assignment,
                   'payload_sha256': authorized_hash, 'inference_calls': 1,
                   'raw_response_sha256': sha_bytes(raw), 'transport_latency_seconds': time.monotonic()-started}
        try:
            response = json.loads(raw)
            receipt.update(response_id=response.get('id'), usage=response.get('usage'), actual_model=response.get('model'), actual_provider=response.get('provider'))
        except (ValueError, AttributeError):
            receipt['parse_error'] = True
        return raw, receipt
    try:
        dump(bundle_path, generate_candidates(provider, requests[assignment]))
        return descriptor(bundle_path)
    except Exception as exc:
        from urllib.error import HTTPError
        if isinstance(exc, HTTPError) and not response_path.exists():
            response_path.write_bytes(exc.read())
        dump(failure_path, {'type': type(exc).__name__, 'retry_allowed': False})
        raise


def admit_sections(directory, bundle_paths, output, review_path=None, base_manifest=None):
    freeze, ctx, extraction, sidecar, requests = verify_sections(directory)
    from .credit_sections import load_section_bundles, section_coverage_ledger
    with v.offline_guard():
        reviews = v.read_json(review_path) if review_path else None
        claims, packet = load_section_bundles(bundle_paths, ctx, extraction, sidecar, requests, reviews=reviews)
        output = new_directory(output)
        dump(output/'section-claims.json', claims)
        dump(output/'atomic-review-proposals.json', packet)
        bundles = [v.read_json(path) for path in bundle_paths]
        dump(output/'section-coverage-ledger.json', section_coverage_ledger(v.read_json(Path(directory)/'section-manifest.json'), requests, bundles))
        dump(output/'review-template.json', {'source_sha256': ctx.content_sha256,
            'canonical_sha256': canonical_hash(extraction), 'bundle_set_sha256': packet['bundle_set_sha256'], 'reviews': {}})
        # Exercise the existing company-intelligence exporter without changing
        # its admission rules: only independently reviewed section atoms enter.
        accepted = [c for c in claims if c['evidence_decision'] == 'SUPPORTED']
        queue = [c for c in claims if c['evidence_decision'] != 'SUPPORTED']
        ledger = {field: {'state': v.field_state(v.coverage_claim_ids(accepted, field)),
                          'claim_ids': v.coverage_claim_ids(accepted, field),
                          'review_claim_ids': [c['claim_id'] for c in queue if field in c['fields']],
                          'completeness': 'PARTIAL' if v.coverage_claim_ids(accepted, field) else 'UNRESOLVED',
                          'reason': 'Supported disclosure present; exhaustive recall not established' if v.coverage_claim_ids(accepted, field) else 'No accepted finding; absence not established'}
                  for field in v.FIELD_NAMES}
        record = {'schema_version': v.VERSION, 'identity': {**ctx.to_dict(), 'company_id': ctx.symbol,
                  'company_id_namespace': 'repository research symbol', 'source_pdf': None, 'rating_agency': None,
                  'reporting_periods': sorted({c['period'] for c in accepted if c.get('period')}),
                  'reporting_periods_note': 'Literal claim period fields; statements may contain additional comparative periods',
                  'field_states': {}, 'evidence': []},
                  'certification': 'offline development slice; not production-certified', 'claims': accepted,
                  'groups': {g: {f: ledger[f'{g}.{f}'] for f in fs} for g, fs in v.GROUPS.items()},
                  'coverage_ledger': ledger, 'evidence_review_queue': queue,
                  'source_coverage': {'physical_pages': len(extraction['pages']),
                    'native_pages': [p['page_or_slide_number'] for p in extraction['pages'] if p['selected_text_origin'] == 'native' and p['selected_text'].strip()],
                    'replayed_claim_pages': [], 'full_document_semantic_review': False,
                    'omissions': ['Section candidates remain provisional until independent source review']},
                  'provenance': {'inputs': [descriptor(path) for path in bundle_paths],
                    'manifest_sha256': freeze['section_manifest_sha256'], 'canonical_sha256': canonical_hash(extraction)},
                  'execution': {'network': 'denied', 'database_connections': 'denied', 'inference_calls': 0,
                                'modes': ['generated_ai_section_replay']}}
        if base_manifest:
            base = v.build_record(base_manifest)
            if (base['identity']['content_sha256'] != ctx.content_sha256 or
                    base['provenance']['canonical_sha256'] != canonical_hash(extraction)):
                raise ValueError('Base Credit V1 record belongs to another source')
            record = deepcopy(base)
            semantic_seen = {canonical_hash({k: c.get(k) for k in (
                'fields','statement','value','unit','period','accounting_scope','attribution',
                'statement_kind','qualifications','conditions')}) for c in record['claims']}
            for claim in accepted:
                key = canonical_hash({k: claim.get(k) for k in (
                    'fields','statement','value','unit','period','accounting_scope','attribution',
                    'statement_kind','qualifications','conditions')})
                if key not in semantic_seen:
                    record['claims'].append(claim); semantic_seen.add(key)
            record['evidence_review_queue'].extend(queue)
            for field in v.FIELD_NAMES:
                ids = v.coverage_claim_ids(record['claims'], field)
                pending = [c['claim_id'] for c in record['evidence_review_queue'] if field in c['fields']]
                record['coverage_ledger'][field] = {'state': v.field_state(ids), 'claim_ids': ids,
                    'review_claim_ids': pending, 'completeness': 'PARTIAL' if ids else 'UNRESOLVED',
                    'reason': 'Supported disclosure present; exhaustive recall not established' if ids else 'No accepted finding; absence not established'}
            record['groups'] = {g: {f: record['coverage_ledger'][f'{g}.{f}'] for f in fs} for g, fs in v.GROUPS.items()}
            record['source_coverage']['section_extraction'] = {
                'schema_version': 'credit-sections/1', 'assignment_count': len(requests),
                'accepted_new_candidates': len(accepted), 'provisional': True,
                'note': 'Original claims and source-review decisions retained; section candidates require independent review.'}
            record['provenance']['inputs'].extend(descriptor(path) for path in bundle_paths)
            record['execution']['modes'].append('generated_ai_section_replay')
        # Export a valid record only after review supplies accepted claims; the
        # empty identity evidence required by the normal complete record is not
        # synthesized here. The section artifacts above are always emitted.
        if accepted:
            cover = extraction['pages'][0]
            from .evidence import text_evidence
            record['identity']['evidence'] = v.attach_evidence(ctx, extraction, [text_evidence(cover, cover['selected_text'])])
            record['identity']['field_states'] = {k: 'DISCLOSED' if record['identity'].get(k) not in (None, []) else 'UNRESOLVED'
                for k in ('company_id','company_name','document_id','content_sha256','rating_agency','report_date','publication_date','filing_date','reporting_periods','source_pdf')}
            v.validate_record(record)
            v.export_record(record, output/'export', ctx.symbol.lower())
        return claims


def prepare(selection_path, output, *, structured=False, full=False):
    with v.offline_guard():
        selection_path = Path(selection_path).resolve()
        selection = v.read_json(selection_path)
        pdf = Path(selection['pdf'])
        if v.digest(pdf) != selection['content_sha256']:
            raise ValueError('Frozen source selection changed')
        for receipt in selection.get('acquisition_receipts', []):
            v.checked_file(ROOT, receipt)
        ext = v.extract_local(pdf.read_bytes(), '.pdf', ocr=False)
        from pypdf import PdfReader
        count = len(PdfReader(str(pdf)).pages)
        if [p['page_or_slide_number'] for p in ext['pages']] != list(range(1, count + 1)):
            raise ValueError('Physical page coverage mismatch')
        ctx = DocumentContext(document_id=selection['document_id'], content_sha256=selection['content_sha256'],
            company_name=selection['company_name'], symbol=selection['symbol'], document_type='credit_rating',
            report_date=selection['report_date'], source_url=selection['source_url'],
            provenance={'acquisition_receipt': descriptor(selection_path), 'metadata_basis': selection['report_date_basis']})
        cover = ext['pages'][0]['selected_text']
        d = date.fromisoformat(ctx.report_date)
        if ctx.company_name not in cover or d.strftime('%B ') + str(d.day) + d.strftime(', %Y') not in cover:
            raise ValueError('Literal company/report date absent from cover; needs source review')
        catalog = build_region_catalog(ctx, ext, version=FULL_VERSION if full else STRUCTURED_VERSION if structured else VERSION)
        request = build_request(ctx, catalog)
        payload = payload_for(request)
        estimate = budget(payload)
        output = new_directory(output)
        dump(output/'canonical.json', ext)
        dump(output/'catalog.json', catalog)
        dump(output/'request.json', request)
        (output/'payload.json').write_bytes(wire_bytes(payload))
        dump(output/'reviews.json', {'source_sha256': ctx.content_sha256, 'claims': {},
                                    'omissions': selection.get('scope_limits', []) + ['No full-document semantic source review completed']})
        manifest = {'company_identifier': selection['symbol'], 'context': asdict(ctx), 'pdf': descriptor(pdf),
                    'canonical': descriptor(output/'canonical.json'), 'reviews': descriptor(output/'reviews.json'), 'responses': []}
        dump(output/'manifest.json', manifest)
        from ..general_ai_experiment.runner import api_key
        freeze = {'schema_version': catalog['schema_version'], 'selection': descriptor(selection_path),
                  'files': {name: descriptor(output/name) for name in ['canonical.json', 'catalog.json', 'request.json', 'payload.json', 'reviews.json', 'manifest.json']},
                  'canonical_sha256': canonical_hash(ext), 'catalog_sha256': canonical_hash(catalog),
                  'payload_sha256': sha_bytes(wire_bytes(payload)), 'budget': estimate,
                  'provider': PROVIDER, 'model': MODEL, 'credential_present': bool(api_key()),
                  'physical_pages': count, 'included_pages': [p['page'] for p in catalog['pages']],
                  'unresolved_region_ids': [r['id'] for r in catalog['regions'] if r['state'] == 'UNRESOLVED'],
                  'inference_calls': 0, 'authorization': 'NOT AUTHORIZED; stop before generate'}
        if full:
            from .credit_coverage import coverage_checklist
            dump(output/'coverage-checklist.json', coverage_checklist(catalog))
        dump(output/'freeze.json', freeze)
        return freeze


def verify_prepared(directory):
    directory = Path(directory).resolve()
    freeze = v.read_json(directory/'freeze.json')
    with v.offline_guard():
        for desc in [freeze['selection'], *freeze['files'].values()]:
            v.checked_file(ROOT, desc)
        m = v.read_json(directory/'manifest.json')
        v.build_record(directory/'manifest.json')
        ctx = DocumentContext(**m['context'])
        ext = v.read_json(directory/'canonical.json')
        catalog = build_region_catalog(ctx, ext, version=freeze['schema_version'])
        request = build_request(ctx, catalog)
        payload = payload_for(request)
        if (catalog != v.read_json(directory/'catalog.json') or request != v.read_json(directory/'request.json') or
                wire_bytes(payload) != (directory/'payload.json').read_bytes() or
                freeze['payload_sha256'] != sha_bytes(wire_bytes(payload)) or freeze['budget'] != budget(payload)):
            raise ValueError('Prepared packet changed')
    return freeze, request, payload


def generate(directory, authorized_hash, ceiling):
    directory = Path(directory).resolve()
    freeze, request, payload = verify_prepared(directory)
    if authorized_hash != freeze['payload_sha256'] or Decimal(ceiling) != Decimal(freeze.get('budget', {}).get('reservation_usd', str(CEILING))):
        raise ValueError('Explicit exact-payload authorization and fixed reservation required')
    from ..general_ai_experiment.runner import api_key, request as transport
    key = api_key()
    if not key:
        raise ValueError('OPENROUTER_API_KEY missing (environment or repository .env)')
    if any((directory/name).exists() for name in ('response.raw.json', 'candidate-bundle.json', 'generation-failure.json')):
        raise FileExistsError('Generation artifacts already exist; no overwrite or repeat send')
    # Exclusive durable reservation survives crashes/errors. No retry or fallback.
    dump(directory/'generation-reservation.json', {'payload_sha256': authorized_hash, 'reserved_usd': freeze.get('budget', {}).get('reservation_usd', str(CEILING)), 'attempts': 1})
    def provider(source_request):
        if source_request != request:
            raise ValueError('Request changed before send')
        started = time.monotonic()
        raw = transport('/chat/completions', payload=payload, key=key, return_raw=True)
        elapsed = time.monotonic() - started
        # Persist bytes before parsing, including malformed/error provider JSON.
        (directory/'response.raw.json').write_bytes(raw)
        receipt = {'model': MODEL, 'provider': PROVIDER, 'payload_sha256': authorized_hash,
                   'inference_calls': 1, 'raw_response_sha256': sha_bytes(raw), 'transport_latency_seconds': elapsed}
        try:
            response = json.loads(raw)
            receipt.update(response_id=response.get('id'), usage=response.get('usage'), actual_model=response.get('model'), actual_provider=response.get('provider'))
        except (ValueError, AttributeError):
            receipt['parse_error'] = True
        return raw, receipt
    try:
        bundle = generate_candidates(provider, request)
        dump(directory/'candidate-bundle.json', bundle)
        return descriptor(directory/'candidate-bundle.json')
    except Exception as exc:
        from urllib.error import HTTPError
        if isinstance(exc, HTTPError):
            with (directory/'response.raw.json').open('xb') as response_file:
                response_file.write(exc.read())
        dump(directory/'generation-failure.json', {'type': type(exc).__name__, 'retry_allowed': False})
        raise


def admit(directory, bundle_path, output, review_path=None):
    directory = Path(directory).resolve()
    verify_prepared(directory)
    with v.offline_guard():
        manifest = v.read_json(directory/'manifest.json')
        manifest['candidate_bundle'] = descriptor(bundle_path)
        if review_path:
            manifest['fresh_reviews'] = descriptor(review_path)
        ctx = DocumentContext(**manifest['context'])
        ext = v.read_json(directory/'canonical.json')
        _, packet = load_candidate_bundle(bundle_path, ctx, ext, reviews=v.read_json(review_path) if review_path else None)
        output = new_directory(output)
        dump(output/'manifest.json', manifest)
        record = v.build_record(output/'manifest.json')
        v.export_record(record, output/'export', manifest['company_identifier'].lower())
        if v.read_json(directory/'freeze.json')['schema_version'] == FULL_VERSION:
            from .credit_coverage import coverage_checklist
            dump(output/'coverage-checklist.json', coverage_checklist(v.read_json(directory/'catalog.json'), record['claims'] + record['evidence_review_queue']))
        dump(output/'atomic-review-proposals.json', packet)
        dump(output/'review-template.json', {'source_sha256': ctx.content_sha256, 'canonical_sha256': canonical_hash(ext),
            'bundle_sha256': canonical_hash(v.read_json(bundle_path)), 'reviews': {}})
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--selection', required=True); p.add_argument('--output', required=True); p.add_argument('--structured', action='store_true'); p.add_argument('--full', action='store_true')
    p = sub.add_parser('generate'); p.add_argument('--prepared', required=True); p.add_argument('--authorize-payload-sha256', required=True); p.add_argument('--max-cost-usd', required=True)
    p = sub.add_parser('admit'); p.add_argument('--prepared', required=True); p.add_argument('--bundle', required=True); p.add_argument('--reviews'); p.add_argument('--output', required=True)
    p = sub.add_parser('prepare-sections'); p.add_argument('--prepared', required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('generate-section'); p.add_argument('--prepared', required=True); p.add_argument('--assignment', choices=('A','B','C','D'), required=True); p.add_argument('--authorize-payload-sha256', required=True); p.add_argument('--max-cost-usd', required=True)
    p = sub.add_parser('admit-sections'); p.add_argument('--prepared', required=True); p.add_argument('--bundle', action='append', required=True); p.add_argument('--reviews'); p.add_argument('--base-manifest'); p.add_argument('--output', required=True)
    for command in ('export', 'replay'):
        p = sub.add_parser(command); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True); p.add_argument('--stem', required=True)
    a = parser.parse_args()
    if a.command == 'prepare':
        print(json.dumps(prepare(a.selection, a.output, structured=a.structured, full=a.full), indent=2))
    elif a.command == 'generate':
        print(json.dumps(generate(a.prepared, a.authorize_payload_sha256, a.max_cost_usd)))
    elif a.command == 'admit':
        record = admit(a.prepared, a.bundle, a.output, a.reviews)
        print(json.dumps({'accepted': len(record['claims']), 'review': len(record['evidence_review_queue'])}))
    elif a.command == 'prepare-sections':
        print(json.dumps(prepare_sections(a.prepared, a.output), indent=2))
    elif a.command == 'generate-section':
        print(json.dumps(generate_section(a.prepared, a.assignment, a.authorize_payload_sha256, a.max_cost_usd)))
    elif a.command == 'admit-sections':
        claims = admit_sections(a.prepared, a.bundle, a.output, a.reviews, a.base_manifest)
        print(json.dumps({'accepted': sum(c['evidence_decision']=='SUPPORTED' for c in claims),
                          'review': sum(c['evidence_decision']!='SUPPORTED' for c in claims)}))
    else:
        v.export_record(v.build_record(a.manifest), a.output, a.stem)


if __name__ == '__main__':
    main()
