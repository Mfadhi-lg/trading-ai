"""Exact-content local campaigns using canonical work and extraction fencing.

No discovery/downloader APIs. The host owns production admission, daily stage
reservations, physical volume identity and scheduling; this module checks the
entire finite selection and uses only existing reconciliation/worker APIs.
"""
from dataclasses import asdict
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
import time
import uuid

from ...db.documents.processing import reconcile_processing
from ...db.repo import utc_now
from ...pipeline.extract_document import processing_version, _load_content, _verified_bytes
from .document_processing import run_processing


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def durable_receipt(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Containers normally restart as PID 1; a retained partial temp file must
    # never prevent recovery of a committed extraction on the next restart.
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def validate(conn, manifest, expected_sha256, version):
    if digest(manifest)!=expected_sha256:raise ValueError('Campaign digest differs')
    if manifest['version']!=asdict(version):raise ValueError('Exact processing version differs')
    if manifest['network_mode']!='none' or manifest['external_requests']!=0:raise ValueError('Local isolation required')
    if manifest['maximum_documents']!=100 or manifest['daily_maximum']!=24:raise ValueError('Campaign bounds differ')
    records=manifest['contents'];ids=[r['content_id'] for r in records]
    if not 1<=len(ids)<=100 or len(set(ids))!=len(ids) or any(type(x) is not int or x<=0 for x in ids):
        raise ValueError('Select 1–100 distinct content IDs')
    for item in records:
        row=_load_content(conn,artifact_id=item['artifact_id'],content_id=item['content_id'])
        for key in ('content_sha256','byte_size','media_type','local_path'):
            if row[key]!=item[key]:raise ValueError('Immutable content identity differs: '+key)
        if item['verification_state']!='verified':raise ValueError('Unverified content')
        artifact=conn.execute('SELECT company_id,artifact_type FROM document_artifacts WHERE artifact_id=?',(item['artifact_id'],)).fetchone()
        if any(artifact[k]!=item[k] for k in ('company_id','artifact_type')):raise ValueError('Artifact identity differs')
        if not conn.execute("SELECT 1 FROM screener_collection_scope WHERE company_id=? AND eligibility='eligible'",(item['company_id'],)).fetchone():
            raise ValueError('Company no longer eligible')
    return records


class Timings:
    """Profile canonical calls without replacing parsers or storage functions."""
    names={'_verified_bytes':'canonical_file_read_and_hash','_load_content':'content_selection',
        'processing_version':'parser_version_startup','extract_pdf':'pdf_extraction_including_ocr',
        'extract_rating_html':'html_extraction','extract_powerpoint':'powerpoint_extraction_including_ocr',
        'persist_extraction':'database_persistence','reconcile_processing':'work_reconciliation',
        'claim_processing':'claim_lock_wait','write_text':'canonical_export'}
    def __init__(self):self.starts={};self.seconds={}
    def profile(self,frame,event,arg):
        name=frame.f_code.co_name
        if name=='extract_text' and frame.f_code.co_filename.endswith('/documents/ocr.py'):
            name='ocr_text'
            self.names['ocr_text']='ocr_seconds'
        if name not in self.names:return
        if event=='call':self.starts[id(frame)]=time.monotonic()
        elif event=='return' and id(frame) in self.starts:
            key=self.names[name];self.seconds[key]=self.seconds.get(key,0)+time.monotonic()-self.starts.pop(id(frame))


def run_local_campaign(*,db_path,manifest,expected_sha256,ocr_engine,output_dir,
                       connection_factory,limit,seconds=300,dry_run=False,now_utc=utc_now):
    if type(limit) is not int or not 1<=limit<=24:raise ValueError('Daily finite limit required')
    if not 1<=seconds<=420:raise ValueError('Finite runtime required')
    start=time.monotonic();outcomes=[]
    if dry_run:
        conn=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
    else:conn=connection_factory(db_path)
    try:
        version=processing_version(ocr_engine)
        records=validate(conn,manifest,expected_sha256,version)
        # Interleave document types so small daily batches remain representative.
        buckets={}
        for item in records:buckets.setdefault(item['artifact_type'],[]).append(item)
        records=[]
        while buckets:
            for kind in list(buckets):
                records.append(buckets[kind].pop(0))
                if not buckets[kind]:del buckets[kind]
        selection_seconds=time.monotonic()-start
        for item in records:
            cached=conn.execute('''SELECT extraction_id,extraction_status FROM document_extractions
                WHERE content_id=? AND parser_name=? AND parser_version=? AND ocr_engine=?
                AND ocr_engine_version=? AND options_json=?''',(item['content_id'],*version.values())).fetchone()
            receipt_path=output_dir/('content-%s.json'%item['content_id'])
            if cached is not None and receipt_path.exists():
                receipt=json.loads(receipt_path.read_text())
                if receipt.get('campaign_sha256')!=expected_sha256 or receipt.get('extraction_id')!=cached['extraction_id']:
                    raise RuntimeError('Durable receipt identity differs')
                continue
            if len(outcomes)>=limit or time.monotonic()-start>=seconds:break
            # Re-read original immutable bytes; never trust an old checkpoint.
            document_started=time.monotonic()
            content=_load_content(conn,artifact_id=item['artifact_id'],content_id=item['content_id'])
            body=_verified_bytes(content);del body
            storage_seconds=time.monotonic()-document_started
            if dry_run:
                outcomes.append({'content_id':item['content_id'],'state':'would_reuse' if cached else 'would_process'})
                continue
            began=time.monotonic();timing=Timings();previous=sys.getprofile()
            try:
                sys.setprofile(timing.profile)
                work=reconcile_processing(conn,content_ids=[item['content_id']],version=version,now=now_utc())[0]
                intent_path=output_dir/('intent-%s.json'%item['content_id'])
                intent_identity={'campaign_sha256':expected_sha256,'content_id':item['content_id'],'work_id':work['work_id']}
                if intent_path.exists():
                    original_intent=json.loads(intent_path.read_text())
                    if any(original_intent.get(k)!=v for k,v in intent_identity.items()):
                        raise RuntimeError('Retained work intent identity differs')
                else:
                    durable_receipt(intent_path,{**intent_identity,'at_utc':now_utc(),'cache_hit':cached is not None})
                outcome=run_processing(db_path=db_path,work_ids=[work['work_id']],ocr_engine=ocr_engine,
                    output_dir=output_dir/'exports',connection_factory=connection_factory,now_utc=now_utc)[0]
            finally:sys.setprofile(previous)
            if outcome.get('state')=='busy':
                outcomes.append({'content_id':item['content_id'],**outcome});continue
            details=conn.execute('''SELECT page_or_slide_count,native_page_count,ocr_page_count,
                unusable_page_count,error_json FROM document_extractions WHERE extraction_id=?''',
                (outcome['extraction_id'],)).fetchone()
            receipt={'campaign_id':manifest['campaign_id'],'campaign_sha256':expected_sha256,
                'content_id':item['content_id'],'artifact_id':item['artifact_id'],
                'content_sha256':item['content_sha256'],'byte_size':item['byte_size'],
                'version':asdict(version),'cache_hit':cached is not None,**outcome,**dict(details),
                'timings':{**timing.seconds,'preclaim_storage_read_and_hash':storage_seconds},
                'worker_seconds':time.monotonic()-began,'document_seconds':time.monotonic()-document_started,
                'selection_seconds':selection_seconds,'finished_at_utc':now_utc(),'external_requests':0}
            durable_receipt(receipt_path,receipt);outcomes.append(receipt)
        return {'campaign_id':manifest['campaign_id'],'outcomes':outcomes,'dry_run':dry_run,
            'total_seconds':time.monotonic()-start,'selected':len(records)}
    finally:conn.close()
