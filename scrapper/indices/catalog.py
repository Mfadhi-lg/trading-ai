"""Read-only SS04 catalog. Candidate membership never implies a built index."""
import hashlib
import json
from pathlib import Path
import sqlite3


def research_catalog(path):
    path = Path(path).resolve()
    connection = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        decisions = list(connection.execute('SELECT * FROM ss04_decisions WHERE major_scope=1'))
        mappings = {}
        for r in connection.execute('SELECT * FROM ss02_security_mapping ORDER BY company_id,isin'):
            evidence=json.loads(r['security_evidence_json'])
            combined=mappings.setdefault(r['company_id'],dict(securities=[],records=[]))
            combined['securities'].extend(evidence.get('securities',[]))
            combined['records'].append(evidence)
        groups = []
        for taxonomy in connection.execute('SELECT * FROM ss04_taxonomy ORDER BY code'):
            members = []
            for r in decisions:
                if r['primary_code'] != taxonomy['code'] or not r['status'].startswith('VERIFIED'):
                    continue
                members.append(dict(company_id=r['company_id'], classification=json.loads(r['decision_json']),
                                    security_evidence=mappings.get(r['company_id']),
                                    exposures=[dict(e) for e in connection.execute('SELECT * FROM ss04_exposures WHERE company_id=?',(r['company_id'],))]))
            groups.append(dict(code=taxonomy['code'], label=taxonomy['label'], members=members,
                               status='UNAVAILABLE_NOT_GENERATED',
                               reason='CANDIDATE_REQUIRES_CANDLE_AND_ACTION_VALIDATION' if len(members)>=2 else 'FEWER_THAN_TWO_VERIFIED_ELIGIBLE_MEMBERS'))
        return dict(source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), groups=groups,
                    completed_indices=0, partial_indices=0, unavailable_indices=len(groups),
                    candidates_with_two_members=sum(len(g['members'])>=2 for g in groups),
                    historical_membership='RETROSPECTIVE_ONLY;NOT_POINT_IN_TIME_CERTIFIED')
    finally:
        connection.close()
