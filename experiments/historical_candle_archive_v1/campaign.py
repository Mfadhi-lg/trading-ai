"""One owner-authorized finite backfill, not a recurring update schedule."""
from collections import Counter,defaultdict
import fcntl
import gzip
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

from .rollout import EVIDENCE,execute,now,acquisition_inventory
from .storage import OUT,ROOT,Archive,dump,sha
from .repair_queue import refresh,dictionaries
from .control import worker,checkpoint,wait as pause_aware_wait,PauseRequested


def run():
    try:
        with worker('acquisition'):
            return _run()
    except PauseRequested:
        return dict(status='PAUSED',reason='OWNER_LIFECYCLE_PAUSE')


def _run():
    authorization=json.loads((EVIDENCE/'owner-authorization.json').read_text())
    native=OUT/'full-universe-manifest.json';special=EVIDENCE/'special-full-manifest.json'
    if (authorization['native_manifest_sha256']!=sha(native.read_bytes()) or
            authorization['session_repair_manifest_sha256']!=sha(special.read_bytes())):
        raise PermissionError('STOP_AUTHORIZED_SCOPE_CHANGED')
    accepted=json.loads((EVIDENCE/'scaled-validation.json').read_text())
    if accepted.get('safe_for_full_manifest') is not True:
        raise RuntimeError('STOP_SCALED_VALIDATION_NOT_ACCEPTED')
    lock=(EVIDENCE/'.full-campaign.lock').open('a+b')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    awake=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    state=dict(started_at=now(),status='RUNNING',phase='NATIVE',native_manifest_sha256=authorization['native_manifest_sha256'],
               session_repair_manifest_sha256=authorization['session_repair_manifest_sha256'],schedules_enabled=False,
               owner_requests_isolated_corruption_be_listed_and_acquisition_continue=True)
    dump(EVIDENCE/'full-campaign.json',state)
    try:
        execute('full',authorize=authorization['native_manifest_sha256'],fast_resume=True)
        state.update(phase='SESSION_REPAIRS',updated_at=now());dump(EVIDENCE/'full-campaign.json',state)
        execute('special-full',authorize=authorization['session_repair_manifest_sha256'],fast_resume=True)
        state.update(phase='FINAL_VERIFICATION',updated_at=now());dump(EVIDENCE/'full-campaign.json',state)
        finalize(state)
        state.update(status='ACQUISITION_PASS_FINISHED; SEE_REPAIR_QUEUE',phase='FINISHED',finished_at=now())
    except BaseException as error:
        state.update(status='STOPPED',reason=str(error) if isinstance(error,RuntimeError) and str(error).startswith('STOP_') else type(error).__name__,updated_at=now())
        if isinstance(error,PauseRequested):state.update(status='PAUSED',reason='OWNER_LIFECYCLE_PAUSE')
        raise
    finally:
        dump(EVIDENCE/'full-campaign.json',state)
        awake.terminate();awake.wait(timeout=10);lock.close()


def finalize(state):
    checkpoint()
    a=Archive()
    try:
        manifest=json.loads((OUT/'full-universe-manifest.json').read_text())
        acquisition_inventory(a.db,manifest,EVIDENCE/'full-inventory.json')
        refresh(a,manifest,'FULL_ACQUISITION_PASS_FINISHED')
        if [r[0] for r in a.db.execute('PRAGMA integrity_check').fetchall()]!=['ok']:
            raise RuntimeError('STOP_FINAL_INDEX_INTEGRITY')
        checked=0
        for digest,path in a.db.execute("SELECT raw_sha256,raw_path FROM requests WHERE status='SUCCESS'").fetchall():
            if checked%500==0:checkpoint();a.guard()
            if sha(gzip.decompress((a.root/path).read_bytes()))!=digest:raise RuntimeError('STOP_FINAL_RAW_CHECKSUM')
            checked+=1
        for path,digest in a.db.execute('SELECT parquet_path,parquet_sha256 FROM datasets').fetchall():
            if checked%500==0:
                checkpoint()
                a.guard();state.update(checksums_verified=checked,updated_at=now());dump(EVIDENCE/'full-campaign.json',state)
            if sha((a.root/path).read_bytes())!=digest:raise RuntimeError('STOP_FINAL_PARQUET_CHECKSUM')
            checked+=1
        source=dictionaries(a.db.execute("""SELECT company_id,timeframe,MIN(first_start) AS first_start,
            MAX(last_start) AS last_start,SUM(row_count) AS retained_rows FROM datasets
            WHERE definition='UPSTOX_NATIVE' AND parquet_path LIKE 'normalized/%'
            AND completeness NOT LIKE 'SUPERSEDED%' GROUP BY company_id,timeframe"""))
        physical=allocated=files=0
        for p in a.root.rglob('*'):
            checkpoint()
            if p.is_file():
                st=p.stat();physical+=st.st_size;allocated+=st.st_blocks*512;files+=1
        initial=json.loads((EVIDENCE/'preservation-before.json').read_text())
        changed=[p for p,d in initial.items() if not (ROOT/p).exists() or sha((ROOT/p).read_bytes())!=d]
        inventory=json.loads((EVIDENCE/'full-inventory.json').read_text())
        repairs=json.loads((EVIDENCE/'repair-queue-summary.json').read_text())
        result=dict(created_at=now(),status='ACQUISITION_PASS_FINISHED; SOURCE_EXCEPTIONS_RETAINED',
            inventory=inventory,source_coverage=source,repair_queue=repairs,checksums_verified=checked,
            sqlite_integrity='ok',preservation_changed_files=changed,storage=dict(logical_bytes=physical,allocated_bytes=allocated,files=files,free_bytes=shutil.disk_usage(a.mount).free),
            full_universe_complete=inventory['successful_requests']==83688 and not repairs['items'],
            schedules_enabled=False,incremental_daily_safe_to_enable=False,
            indicator_compatibility='Existing engines passed scaled clean-window checks; weekly DSS/RS limitations and adjustment/calendar flags remain',
            history_assumptions='Provider retrospective revisions; receipts never backdated; intraday prefixes are not historical live snapshots')
        dump(EVIDENCE/'full-completion.json',result)
        (EVIDENCE/'FULL-ACQUISITION-RESULT.md').write_text(
            '# HCD-02 — full acquisition pass\n\n'
            f"Native requests successful: {inventory['successful_requests']:,}; failed: {inventory['failed_requests']:,}. "
            f"Companies with four planned nonempty native histories: {inventory['companies_with_all_four_planned_native_histories']:,}/{inventory['expected_companies']:,}.\n\n"
            f"Owner repair queue: {repairs['items']:,} items across {repairs['companies']:,} companies. "
            'See `repair-queue.csv`, `full-inventory.json` and `full-completion.json` for actual per-company results, source dates, reasons and original-response paths. '
            'A completed acquisition pass is not a claim that quarantined or unsupported history is usable.\n\n'
            f"Archive logical bytes: {physical:,}; allocated file bytes: {allocated:,}; checksums verified: {checked:,}; SQLite integrity: ok.\n\n"
            'Schedules remain disabled. Known calendar, historical eligibility, security continuity and corporate-action uncertainties remain explicit. '
            'Original provider responses are on Passport; no bad observation was repaired with an invented value.\n')
    finally:
        a.db.close();a.writer_lock.close()


def watch_report():
    try:
        with worker('reporter'):
            return _watch_report()
    except PauseRequested:
        return


def _watch_report():
    """One finite completion reporter, leaving the acquisition process alone."""
    from .access import HistoricalStore
    from .hcd02_review import special_coverage
    from .storage import verify
    awake=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while True:
            checkpoint()
            state=json.loads((EVIDENCE/'full-campaign.json').read_text())
            if state['status']!='RUNNING':break
            pause_aware_wait(30)
        store=HistoricalStore()
        try:
            manifest=json.loads((OUT/'full-universe-manifest.json').read_text())
            if state.get('phase')=='FINISHED':
                expanded=dict(manifest,companies=manifest['companies']+manifest.get('historically_eligible_extension',[]))
                special_coverage(store,expanded,'full-special-session-coverage.json',checkpoint=checkpoint)
            adapter=SimpleNamespace(root=store.root,db=store.db,guard=lambda:verify(store.mount,store.device))
            refresh(adapter,manifest,'FINAL_OWNER_REPAIR_REPORT')
            execution=json.loads((EVIDENCE/'full-execution.json').read_text())
            inventory=json.loads((EVIDENCE/'full-inventory.json').read_text())
            start=execution['started_at'];ids={q['request_id'] for q in manifest['requests']}
            rows=dictionaries(store.db.execute('SELECT request_id,status,received_at FROM requests'))
            new=sum(r['request_id'] in ids and r['status']=='SUCCESS' and (r['received_at'] or '')>=start for r in rows)
            from datetime import datetime
            elapsed=(datetime.fromisoformat(execution['updated_at'])-datetime.fromisoformat(start)).total_seconds()
            repairs=json.loads((EVIDENCE/'repair-queue-summary.json').read_text())
            full=EVIDENCE/'full-completion.json'
            result=json.loads(full.read_text()) if full.exists() else dict(status=state['status'],stop_reason=state.get('reason'),inventory=inventory)
            result.update(owner_authorization_retained=True,repair_queue=repairs,
                throughput=dict(new_native_successful_requests=new,gross_native_seconds=elapsed,requests_per_second=new/elapsed if elapsed else None),
                final_owner_report_created_at=now(),schedules_enabled=False,incremental_daily_safe_to_enable=False)
            dump(full,result)
            report=EVIDENCE/'FULL-ACQUISITION-RESULT.md'
            with report.open('a') as f:
                f.write(f"\nFinal owner repair list: {repairs['items']:,} items across {repairs['companies']:,} companies. "
                    f"Native acquisition throughput: {new:,} new completed requests in {elapsed:.2f}s. "
                    'See repair-queue.csv for corruption, failed requests, unsupported history, mapping and session-calendar holds. '
                    'Full split-session coverage is retained separately; source absence may be pre-listing and is not labeled a corrupt trade.\n')
            dump(EVIDENCE/'owner-repair-report-ready.json',dict(created_at=now(),acquisition_state=state['status'],
                repair_list=str(EVIDENCE/'repair-queue.csv'),completion_report=str(report),items=repairs['items']))
        finally:store.close()
    finally:
        awake.terminate();awake.wait(timeout=10)
