"""Evidence-backed names for rating reports attached to a verified company."""
import json
import re
from pathlib import Path


def name_key(text):
    text = text.lower().replace('&', 'and')
    text = re.sub(r'\bco\b\.?', 'company', text)
    text = re.sub(r'\b(?:limited|ltd|private|pvt|the)\b', ' ', text)
    return re.sub(r'[^a-z0-9]', '', text)


def company_names(conn, artifact_id):
    """Use official exchange history linked by security identity, never filename guesses."""
    row = conn.execute('''SELECT company_id,current_legal_name FROM document_artifacts
        JOIN companies USING(company_id) WHERE artifact_id=?''', (artifact_id,)).fetchone()
    if row is None:
        raise ValueError('Rating artifact has no verified company identity')
    company_id, current_name = row['company_id'], row['current_legal_name']
    result = [{'name': current_name, 'basis': 'current_company_identity'}]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'exchange_company_registry', 'exchange_company_links', 'exchange_security_versions',
            'exchange_security_events', 'exchange_source_snapshots'} <= tables:
        # The registry and exchange link are joined through ISIN, rather than text similarity.
        for r in conn.execute('''SELECT DISTINCT v.name_raw,v.snapshot_id
            FROM exchange_company_registry cr JOIN exchange_company_links cl USING(registry_company_id)
            JOIN exchange_security_versions v USING(exchange_security_id)
            JOIN exchange_source_snapshots s ON s.snapshot_id=v.snapshot_id
            WHERE cr.company_id=? AND cl.is_current=1 AND s.validation_status='passed' ''', (company_id,)):
            result.append({'name': r['name_raw'], 'basis': 'official_exchange_name',
                           'snapshot_id': r['snapshot_id']})
        for r in conn.execute('''SELECT DISTINCT e.from_value,e.security_event_id,e.snapshot_id
            FROM exchange_company_registry cr JOIN exchange_company_links cl USING(registry_company_id)
            JOIN exchange_security_events e USING(exchange_security_id)
            JOIN exchange_source_snapshots s ON s.snapshot_id=e.snapshot_id
            WHERE cr.company_id=? AND cl.is_current=1 AND e.event_type='name_changed'
                AND s.validation_status='passed' ''', (company_id,)):
            if r['from_value']:
                result.append({'name': r['from_value'], 'basis': 'official_exchange_name_change',
                    'security_event_id': r['security_event_id'], 'snapshot_id': r['snapshot_id']})
    catalog = Path(__file__).with_name('rating_company_aliases.json')
    if catalog.exists():
        for entry in json.loads(catalog.read_text()):
            if name_key(entry['current_name']) == name_key(current_name):
                for alias in entry['aliases']:
                    result.append({'name': alias, 'basis': 'verified_primary_source',
                                   'source_url': entry['source_url']})
    unique = {}
    for entry in result:
        if name_key(entry['name']):
            unique.setdefault(name_key(entry['name']), entry)
    return list(unique.values())


def match_company(text, company_name, aliases=()):
    key = name_key(text[:20000])
    names = [{'name': company_name, 'basis': 'current_company_identity'}, *aliases]
    for entry in names:
        name = name_key(entry['name'])
        if not name:
            continue
        if len(name) <= 5:
            # Short legal-name acronyms (e.g. HIL) must not match inside "while".
            matched = re.search(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])',
                                text[:20000], re.I)
        else:
            matched = name in key
        if matched:
            return entry
    return None
