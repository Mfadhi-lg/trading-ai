"""Reproducible manifest from authoritative membership and local original bytes.

Selection is explicit, never driven by financial performance. Missing PDF slots
remain missing; HTML rating rationales are not relabeled as PDF originals.
"""
import argparse
import json
from pathlib import Path
import subprocess
from pypdf import PdfReader
from .provider import digest, save, stable

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = Path('/Volumes/My Passport/passport/trading-ai/artifact_content')
TYPES = ('credit_rating', 'presentation', 'transcript')
SELECTION = (
    ('VIMTALABS', 'development', {'credit_rating': 'CARE_Vimta', 'presentation': 'Vimta_Labs', 'transcript': 'Vimta_Labs'}),
    ('EKC', 'development', {'credit_rating': 6169, 'presentation': 'Everest_Kanto_Cylinder_Q4', 'transcript': 'Everest_Kanto_Cylinder_Q4'}),
    ('IDFCFIRSTB', 'evaluation', {'credit_rating': 'Brickwork_IDFC', 'presentation': 28536, 'transcript': 28515}),
    ('RECLTD', 'evaluation', {'credit_rating': 'CARE_REC', 'presentation': 27737, 'transcript': 27719}),
)


def discover(output):
    output = Path(output)
    if output.exists(): raise ValueError('Manifest directory must be new')
    membership = {}
    for group in ('in_q1_focus_list', 'not_in_q1_focus_list'):
        source = ROOT / 'sample_files-2' / group / 'manifest.json'
        for r in json.loads(source.read_text()):
            symbol = r.get('symbol')
            if not symbol: continue
            entry = membership.setdefault(symbol, dict(company_name=r['company_name'],
                focus_list_member=r['q1_focus_member'], membership_manifest=str(source),
                membership_manifest_sha256=digest(source.read_bytes()), rows=[]))
            if entry['focus_list_member'] != r['q1_focus_member']: raise ValueError('Membership conflict')
            entry['rows'].append(dict(r, local_path=str(source.parent / r['file'])))
    result = subprocess.run(['rg', '-l', '-i', 'Vimta Labs|IDFC First|Sona BLW|Everest Kanto|REC Limited|Walchandnagar',
        str(ROOT / 'artifacts/document-acquisition'), '--glob', 'artifact-*.json'], capture_output=True, text=True)
    if result.returncode not in (0, 1): raise RuntimeError('Archive index search failed')
    exports = {}
    for p in sorted(result.stdout.splitlines()):
        d = json.loads(Path(p).read_text())
        exports[d['artifact']['artifact_id']] = (p, d)
    companies = []
    metadata_path=Path(__file__).with_name('document_metadata.json')
    annotations=json.loads(metadata_path.read_text())
    for symbol, split, selected in SELECTION:
        m = membership[symbol]
        company = {k: v for k, v in m.items() if k != 'rows'}
        company.update(company_id=symbol, split=split, documents=[], missing_documents=[])
        for kind in TYPES:
            choice = selected.get(kind)
            if choice is None:
                company['missing_documents'].append(dict(document_type=kind,
                    reason='Suitable original PDF not located in sample manifests or searched retained acquisition exports',
                    search_scope='bounded local index search; not proof that no such document exists anywhere on disk'))
                continue
            if isinstance(choice, int):
                export_path, payload = exports[choice]
                if payload['artifact']['artifact_type'] != kind or payload['content']['media_type'] != 'application/pdf':
                    raise ValueError('Selected archived artifact is not required PDF type')
                sha = payload['content']['content_sha256']
                path = ARCHIVE / sha[:2] / (sha + '.pdf')
                source = dict(archive_export=export_path, artifact_id=choice,
                    archive_export_sha256=digest(Path(export_path).read_bytes()))
            else:
                folder = {'credit_rating':'credit_ratings', 'presentation':'presentations', 'transcript':'concall_transcripts'}[kind]
                rows = [r for r in m['rows'] if r['file'].startswith(folder + '/') and choice in r['file']]
                if len(rows) != 1: raise ValueError('Ambiguous explicit sample selection')
                row = rows[0]; sha = row['sha256']; path = Path(row['local_path'])
                source = dict(source_local_file=row.get('source_local_file'), source_url=row.get('source_url'))
            body = path.read_bytes()
            if not body.startswith(b'%PDF') or digest(body) != sha: raise ValueError('Original hash/signature mismatch')
            reader = PdfReader(path)
            # Metadata inspection only: first two physical pages, no evaluation extraction/prompt optimization.
            covers = [dict(physical_page=i+1, text=reader.pages[i].extract_text() or '') for i in range(min(2,len(reader.pages)))]
            history = subprocess.run(['rg', '-l', '-F', sha, str(ROOT/'artifacts/phase5'),
                '--glob', '*.json', '--glob', '*.csv', '--glob', '*.md'], capture_output=True, text=True)
            if history.returncode not in (0,1): raise RuntimeError('Exposure audit failed')
            doc = dict(document_id=symbol+'-'+kind, document_type=kind, source_path=str(path.resolve()),
                source_filename=path.name, sha256=sha, physical_pages=len(reader.pages), bytes=len(body),
                publication_date=None, publication_date_status='not independently established',
                document_date=None, reporting_period=None, metadata_source_pages=covers,
                metadata_status='Cover text retained; date/period metadata requires source review',
                prior_exposure_paths=history.stdout.splitlines(),
                exposure_status='previously_referenced' if history.stdout else 'no_hash_match_in_bounded_history_search',
                genuinely_unseen=False, **source)
            annotation=annotations.get(doc['document_id'],{})
            for anchor in annotation.get('metadata_anchors',[]):
                if anchor['quote'] not in covers[anchor['physical_page']-1]['text']:
                    raise ValueError('Metadata source quote mismatch: '+doc['document_id'])
            doc.update(annotation,metadata_status='Source cover metadata inspected; publication time not independently established',
                metadata_annotations_sha256=digest(metadata_path.read_bytes()))
            company['documents'].append(doc)
        companies.append(company)
    if len(companies) != 4 or sum(c['focus_list_member'] for c in companies) != 2:
        raise ValueError('Revised pilot requires exactly two companies from each membership manifest')
    manifest = dict(schema_version='unified-pilot/0.1', companies=companies,
        selection_status='complete_four_company_sample_user_revised',
        missing_policy='No silent substitution; evaluation cannot satisfy three-document acceptance until missing PDFs resolved',
        evaluation_policy='Frozen selection; evaluation content not used for development tuning; prior exposure is not unseen',
        discovery_scope='sample_files-2 membership manifests, retained acquisition exports, hash-addressed original archive',
        source_root=str(ROOT), archive_root=str(ARCHIVE))
    output.mkdir(parents=True)
    save(output/'pilot_manifest.json',manifest)
    save(output/'selection_freeze.json',dict(manifest_sha256=digest(stable(manifest).encode()),
        evaluation_company_ids=[c['company_id'] for c in companies if c['split']=='evaluation']))
    return manifest


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    args=parser.parse_args(); m=discover(args.output)
    print(json.dumps({c['company_id']:dict(located=len(c['documents']),missing=c['missing_documents']) for c in m['companies']},indent=2))
