"""Read-only gate for a new arrival with actual launchd stage receipts."""
from datetime import datetime
from pathlib import Path
import hashlib
import json


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Evidence timestamps must be timezone aware')
    return result


def verify(conn, *, receipts, epoch, expires=None):
    start = instant(epoch)
    end = instant(expires) if expires else None
    stages = {}
    for folder in Path(receipts).glob('*-document-*'):
        if not (folder/'admission.json').is_file() or not (folder/'outcome.json').is_file():
            continue
        admission = json.loads((folder/'admission.json').read_text())
        outcome = json.loads((folder/'outcome.json').read_text())
        admitted = instant(admission['started_at_utc'])
        if (admission.get('dispatch_origin') != 'launchd' or admitted < start
                or (end and admitted >= end) or outcome.get('status') != 'PASS'):
            continue
        log = folder/'runtime.log'
        for line in log.read_text().splitlines() if log.exists() else ():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('stage') != 'document_stage_outcome' or not record.get('protected_unchanged'):
                continue
            stages.setdefault(record['document_stage'], []).append((record, str(folder), admitted))
    downloads = {('generic', j['download_job_id']): (folder, admitted, instant(r['finished_at_utc']))
                 for r, folder, admitted in stages.get('DOCUMENT_DOWNLOAD', [])
                 for j in r['result'].get('selected', [])}
    contents = {j['content_id']: (folder, admitted, instant(r['finished_at_utc']), r['result'].get('outcomes', []))
                for r, folder, admitted in stages.get('DOCUMENT_PROCESSING', [])
                for j in r['result'].get('selected', [])}
    # Dedicated rating jobs use a different queue. Correlate actual resolver
    # attempts with the bounded launchd stage interval, never aggregate counts.
    for r, folder, admitted in stages.get('DOCUMENT_RATING', []):
        finished = instant(r['finished_at_utc'])
        for attempt in conn.execute('SELECT download_job_id,started_at_utc,completed_at_utc FROM credit_rating_resolution_attempts'):
            if attempt['completed_at_utc'] and admitted <= instant(attempt['started_at_utc']) <= instant(attempt['completed_at_utc']) <= finished:
                downloads[('rating', attempt['download_job_id'])] = (folder, admitted, finished)
    lineage = []
    for key in downloads:
        kind, job = key
        acquisition = ("FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id) "
                       "JOIN artifact_download_queue_attempts qa USING(download_job_id) "
                       "JOIN artifact_download_attempts da USING(download_attempt_id)" if kind == 'generic' else
                       "FROM credit_rating_download_queue q JOIN document_artifacts a USING(artifact_id) "
                       "JOIN credit_rating_resolution_attempts ra USING(download_job_id,artifact_id) "
                       "JOIN artifact_download_attempts da ON da.artifact_id=a.artifact_id "
                       "AND da.started_at_utc=ra.started_at_utc AND da.final_url=ra.final_url")
        rows = conn.execute('''SELECT q.download_job_id,a.artifact_id,a.company_id,a.artifact_type,a.period_end,
                a.published_at_utc,a.first_observed_at_utc,c.content_id,c.content_sha256,c.created_at_utc,b.local_path,b.byte_size,
                w.work_id,w.extraction_id,e.extraction_status,s.discovered_from_source,s.discovery_page_url,
                s.document_url,s.artifact_source_id,s.discovery_page_id,expectation.expectation_id,event.event_key,
                event.evidence_source,event.evidence_json,da.started_at_utc download_started,da.fetched_at_utc download_finished,
                e.created_at_utc extraction_created,e.completed_at_utc extraction_finished
            ''' + acquisition + '''
            JOIN artifact_contents c ON c.content_id=da.content_id AND c.artifact_id=a.artifact_id
            JOIN response_blobs b ON b.blob_id=c.blob_id
            JOIN document_processing_inbox i ON i.content_id=c.content_id
            JOIN document_processing_work w ON w.content_id=c.content_id
            JOIN document_extractions e ON e.extraction_id=w.extraction_id AND e.content_id=c.content_id
            JOIN artifact_sources s ON s.artifact_id=a.artifact_id AND s.document_url=q.requested_url
            JOIN artifact_download_attempt_sources ds ON ds.download_attempt_id=da.download_attempt_id AND ds.artifact_source_id=s.artifact_source_id
            JOIN artifact_discovery_pages p ON p.discovery_page_id=s.discovery_page_id AND p.company_id=a.company_id
                AND p.discovered_from_source=s.discovered_from_source AND p.discovery_page_url=s.discovery_page_url
            JOIN artifact_expectations expectation ON expectation.company_id=a.company_id AND expectation.artifact_type=a.artifact_type
            JOIN artifact_events event ON event.event_id=expectation.event_id AND event.company_id=a.company_id AND event.period_end=a.period_end
            WHERE q.download_job_id=? AND q.status='SUCCEEDED' AND a.artifact_id<>81417
              AND da.result_status='validated' AND e.extraction_status='ok'
              AND event.evidence_source IN ('exchange_result_filing','d01_artifact_discovery')''', (job,)).fetchall()
        for row in rows:
            if row['content_id'] not in contents:
                continue
            download_folder, download_start, download_end = downloads[key]
            processing_folder, processing_start, processing_end, outcomes = contents[row['content_id']]
            if not (download_start <= instant(row['download_started']) <= instant(row['download_finished']) <= download_end
                    and processing_start <= instant(row['extraction_created']) <= instant(row['extraction_finished']) <= processing_end):
                continue
            if not any(o.get('work_id') == row['work_id'] and o.get('extraction_id') == row['extraction_id']
                       and o.get('extraction_status') == 'ok' for o in outcomes):
                continue
            # Newly stored bytes for an old artifact do not establish new arrival.
            if instant(row['first_observed_at_utc']) < start or instant(row['created_at_utc']) < start:
                continue
            if row['evidence_source'] == 'd01_artifact_discovery':
                if (row['discovered_from_source'] not in ('company_ir', 'nse', 'bse')
                        or row['artifact_id'] not in json.loads(row['evidence_json']).get('artifact_ids', [])):
                    continue
            discovery = []
            for r, folder, admitted in stages.get('DOCUMENT_DISCOVERY', []):
                observations = r['result'].get('official', {}).get('observations', [])
                if any(o.get('company_id') == row['company_id']
                       and row['artifact_id'] in o.get('artifact_ids', [])
                       and row['artifact_source_id'] in o.get('artifact_source_ids', []) for o in observations):
                    discovery.append(folder)
                elif any(j['expectation_id'] == row['expectation_id'] for j in r['result'].get('selected', [])):
                    discovery.append(folder)
            if not discovery:
                continue
            path = Path(row['local_path'])
            if path.stat().st_size != row['byte_size'] or hashlib.sha256(path.read_bytes()).hexdigest() != row['content_sha256']:
                raise RuntimeError('Unattended archived original failed verification')
            item = dict(row)
            item['download_queue_kind'] = kind
            item['actual_launchd_receipts'] = sorted(set(discovery + [download_folder, processing_folder]))
            lineage.append(item)
    return {'status': 'PASS' if lineage else 'PENDING_UNATTENDED_NEW_DOCUMENT_PROOF',
            'lifecycle': lineage,
            'actual_launchd_receipts': sorted({folder for entries in stages.values() for _, folder, _ in entries}),
            'stages_observed': sorted(stages), 'excluded_canary_artifact': 81417,
            'scheduler_ticks_are_not_execution_evidence': True}
