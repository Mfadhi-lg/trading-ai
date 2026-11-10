"""Frozen finite extraction campaigns; never follow arrivals or retire legacy roles."""
from dataclasses import asdict
import hashlib
import json
import sqlite3

from ...config import assert_configured_writable_targets
from ...db.database import connect, selected_backend
from ...db.documents.processing import ProcessingVersion, reconcile_processing
from ...db.repo import utc_now
from ...pipeline.extract_document import processing_version
from .document_processing import run_processing


def read_connection(db_path):
    if selected_backend() == 'postgresql':
        conn = connect(db_path, read_only=True)
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return conn
    conn = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")  # Stable read-only inventory/preflight snapshot.
    return conn


def digest(campaign):
    return hashlib.sha256(json.dumps(campaign, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _content(conn, content_id):
    row = conn.execute('''SELECT c.content_id,c.artifact_id,c.content_sha256,
        c.content_origin,c.media_type,a.artifact_type FROM artifact_contents c
        JOIN document_artifacts a USING(artifact_id) WHERE content_id=?''',
        (content_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown campaign content')
    return dict(row)


def _browser(conn, run_id, ids):
    jobs = []
    for job_id in ids:
        row = conn.execute('SELECT * FROM browser_rating_recovery_jobs WHERE run_id=? AND browser_job_id=?',
            (run_id, job_id)).fetchone()
        if row is None or row['status'] not in ('SUCCEEDED', 'SKIPPED_EXISTING') or row['content_id'] is None:
            raise ValueError('Select imported jobs from this browser run')
        evidence = json.loads(row['evidence_json'])
        contents = [row['content_id']]
        if evidence.get('rendered_content_id') is not None:
            contents.append(evidence['rendered_content_id'])
        for cid in contents:
            if type(cid) is not int or cid <= 0 or _content(conn, cid)['artifact_id'] != row['artifact_id']:
                raise ValueError('Browser original/derivative artifact mismatch')
        jobs.append({'browser_job_id':job_id, 'artifact_id':row['artifact_id'],
            'content_ids':list(dict.fromkeys(contents))})
    return jobs


def _ids(ids):
    values = tuple(ids)
    if not 1 <= len(values) <= 100 or len(set(values)) != len(values) or any(type(x) is not int or x <= 0 for x in values):
        raise ValueError('Select 1–100 distinct positive IDs')
    return values


def freeze_campaign(conn, *, campaign_id, version, period=None, run_id=None,
                    browser_job_ids=(), limit=100, quarter_selector=None):
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValueError('Campaign identity required')
    if (period is None) == (run_id is None):
        raise ValueError('Select exactly one quarter or browser run')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Finite limit must be 1–100')
    if period is not None:
        if browser_job_ids:
            raise ValueError('Browser selection is not a quarter selection')
        if quarter_selector is None:
            raise ValueError("Explicit existing quarter selector required")
        selected, backlog = quarter_selector(conn, period=period, limit=limit)
        jobs = [{'artifact_id':aid, 'content_ids':[cid]} for aid,cid in selected]
        scope = {'kind':'quarter', 'period':period}
        accounting = {'candidate_artifacts':backlog, 'selected_artifacts':len(jobs),
            'unselected_or_ineligible_artifacts':backlog-len(jobs)}
    else:
        ids = _ids(browser_job_ids)
        if len(ids) > limit or not isinstance(run_id,str) or not run_id.strip():
            raise ValueError('Invalid finite browser selection')
        jobs = _browser(conn, run_id, ids)
        scope = {'kind':'browser', 'run_id':run_id}
        counts = dict(conn.execute('SELECT status,count(*) FROM browser_rating_recovery_jobs WHERE run_id=? GROUP BY status', (run_id,)).fetchall())
        accounting = {'run_states':counts, 'selected_jobs':len(jobs),
            'unselected_jobs':sum(counts.values())-len(jobs)}
    cids = list(dict.fromkeys(cid for job in jobs for cid in job['content_ids']))
    return {'schema_version':1, 'campaign_id':campaign_id, 'scope':scope,
        'version':asdict(version), 'jobs':jobs, 'contents':[_content(conn,cid) for cid in cids],
        'snapshot_accounting':accounting,
        'retirement_authorized':False,
        'residual_stages':['browser rating normalization/evidence and derivative repair'] if run_id else []}


def validate_campaign(conn, campaign, *, expected_sha256, version):
    if digest(campaign) != expected_sha256:
        raise ValueError('Campaign checksum does not match reviewed selection')
    if campaign['schema_version'] != 1 or campaign['version'] != asdict(version):
        raise ValueError('Campaign processing version mismatch')
    if campaign['retirement_authorized'] is not False:
        raise ValueError('Campaign cannot authorize role retirement')
    jobs = campaign['jobs']
    if len(jobs)>100:
        raise ValueError('Campaign has too many jobs')
    cids = list(dict.fromkeys(cid for j in jobs for cid in j['content_ids']))
    if cids:
        if len(cids)>200 or any(type(cid) is not int or cid<=0 for cid in cids):
            raise ValueError('Invalid campaign content selection')
    actual = [_content(conn,cid) for cid in cids]
    if actual != campaign['contents']:
        raise ValueError('Campaign content identity changed')
    for j in jobs:
        if not j['content_ids'] or any(_content(conn,cid)['artifact_id']!=j['artifact_id'] for cid in j['content_ids']):
            raise ValueError('Campaign artifact mismatch')
    scope = campaign['scope']
    if scope['kind']=='browser':
        if _browser(conn,scope['run_id'],_ids(j['browser_job_id'] for j in jobs)) != jobs:
            raise ValueError('Browser selected provenance changed; freeze a new campaign')
    elif scope['kind']!='quarter':
        raise ValueError('Unknown campaign kind')
    return cids


def campaign_status(conn, campaign):
    version = ProcessingVersion(**campaign['version'])
    records = []
    for content in campaign['contents']:
        row = conn.execute('''SELECT w.work_id,e.extraction_id,e.extraction_status
            FROM document_processing_work w LEFT JOIN document_extractions e USING(extraction_id)
            WHERE w.resource_key=?''', (version.key(content['content_id']),)).fetchone()
        supported = content['media_type'] in ('application/pdf','application/vnd.ms-powerpoint',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation') or (
            content['media_type']=='text/html' and content['artifact_type']=='credit_rating')
        state = 'unsupported' if not supported else 'not_enqueued' if row is None else 'pending' if row['extraction_id'] is None else row['extraction_status']
        records.append({'content_id':content['content_id'], 'state':state,
            'work_id':row['work_id'] if row else None, 'extraction_id':row['extraction_id'] if row else None})
    outstanding = sum(r['state'] in ('not_enqueued','pending') for r in records)
    return {'campaign_id':campaign['campaign_id'], 'records':records, 'outstanding':outstanding,
        'extraction_accounted':outstanding==0,
        'retirement_authorized':False, 'residual_stages':campaign['residual_stages'],
        'snapshot_accounting':campaign['snapshot_accounting']}


def run_campaign(*, db_path, campaign, expected_sha256, ocr_engine, output_dir,
                 limit=100, lifecycle=None, now_utc=utc_now):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Finite work budget must be 1–100')
    actual = processing_version(ocr_engine)
    ro = read_connection(db_path)
    try:
        cids = validate_campaign(ro,campaign,expected_sha256=expected_sha256,version=actual)
    finally:
        ro.close()
    # Entire identity/version selection checked before any writer or directory.
    assert_configured_writable_targets(output_dir, db_path=db_path)
    conn = connect(db_path)
    outcomes = []
    try:
        if lifecycle is None or not lifecycle.is_set():
            for start in range(0,len(cids),100):
                reconcile_processing(conn,content_ids=cids[start:start+100],version=actual,now=now_utc())
            status = campaign_status(conn,campaign)
            ids = [r['work_id'] for r in status['records'] if r['state']=='pending'][:limit]
            if ids:
                outcomes = run_processing(db_path=db_path,work_ids=ids,ocr_engine=ocr_engine,
                    output_dir=output_dir,lifecycle=lifecycle,now_utc=now_utc)
        return {**campaign_status(conn,campaign), 'outcomes':outcomes}
    finally:
        conn.close()
