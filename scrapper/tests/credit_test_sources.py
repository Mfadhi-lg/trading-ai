"""Invented original PDFs and matching canonical evidence for portable tests."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from scrapper.research.document_intelligence import credit_v1 as v
from scrapper.research.document_intelligence import credit_candidate_provider as provider
from scrapper.research.document_intelligence import credit_sections as sections
from scrapper.research.document_intelligence.schema import DocumentContext


def create_source(root):
    """Create original bytes and locators without claiming any real issuer provenance."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    texts = [
        'Synthetic Limited\nMay 1, 2026\nRating rationale\nAgency assesses liquidity adequate, subject to sanctioned limits and drawing power.\nRevenue rose in FY2026 because volumes increased.\n',
        'Liquidity: Adequate\nCash was Rs. 8 crore at March 31, 2026.\nAgency expects capex of Rs. 30 crore in FY2027, only if internal accruals suffice.\nThe agency does not conduct an audit.\n',
    ]
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 40 740 Td ' + ' 0 -18 Td '.join('(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj' for line in text.splitlines()) + ' ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    pdf = root / 'original.pdf'
    writer.write(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    inventory = root / 'inventory.json'
    inventory.write_text(json.dumps({'source': 'invented portable software fixture', 'not_real_financial_evidence': True}))
    ctx = DocumentContext(document_id='synthetic-credit', company_name='Synthetic Limited', symbol='SYNTHETIC', content_sha256=digest, document_type='credit_rating', report_date='2026-05-01', provenance={'source_inventory': {'path': 'inventory.json', 'sha256': v.digest(inventory)}})
    extraction = {'extraction_status': 'ok', 'parser_name': 'synthetic', 'parser_version': '1', 'pages': [{'page_kind': 'page', 'page_or_slide_number': i, 'selected_text': text, 'selected_text_origin': 'native', 'elements': []} for i, text in enumerate(texts, 1)]}
    catalog = provider.build_region_catalog(ctx, extraction, version=provider.FULL_VERSION)
    sidecar, _ = sections.build_section_material(ctx, extraction, catalog)
    for name, value in [('context.json', asdict(ctx)), ('canonical.json', extraction), ('sidecar.json', sidecar), ('reviews.json', {'source_sha256': digest, 'claims': {}, 'omissions': []})]:
        (root / name).write_text(json.dumps(value, ensure_ascii=False))
    manifest = {'company_identifier': 'SYNTHETIC', 'context': asdict(ctx), 'pdf': {'path': 'original.pdf', 'sha256': digest}, 'canonical': {'path': 'canonical.json', 'sha256': v.digest(root / 'canonical.json')}, 'reviews': {'path': 'reviews.json', 'sha256': v.digest(root / 'reviews.json')}, 'responses': []}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return ctx, extraction, sidecar
